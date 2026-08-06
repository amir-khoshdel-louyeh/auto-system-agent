# Architecture (Phase 10 item [90], extended by [104]/[105])

Diagrams are Mermaid so they render in the repository view and can be diffed
like code. Sections 2 and 4 include the Phase 11 additions: the launcher that
makes `python main.py` one command, and the publish → run → poll routes the
workbench now uses. Every box names the module that implements it; every state machine
is copied from the frozen vocabulary in `src/engine/executor/states.py` and
`src/app/approvals.py`, not invented for the picture.

---

## 1. System context

```mermaid
flowchart LR
  subgraph people["People"]
    OP["Operator / OWNER<br/>authors and runs workflows"]
    AP["Approver<br/>decides gated nodes"]
    AU["Auditor / VIEWER<br/>reads runs and evidence"]
  end
  subgraph system["Auto System Agent (MVP)"]
    API["HTTP API<br/>src/app"]
    BUS["Event bus + workers<br/>src/bus"]
    ENG["DAG executor<br/>src/engine"]
    SAFE["Policy + approvals<br/>src/safety, src/app/approvals.py"]
    SBX["Sandbox<br/>src/sandbox"]
    MEM["Shared memory<br/>src/memory"]
    AUD["Audit chain + metrics<br/>src/storage"]
  end
  LLM["Optional LLM provider<br/>src/orchestration/llm_adapter.py"]
  FS["Workspace filesystem"]

  OP -->|"YAML DSL, dry run, run"| API
  AP -->|"decision"| API
  AU -->|"timeline, audit, verify"| API
  API --> ENG
  API --> MEM
  API --> AUD
  ENG --> SAFE
  ENG --> BUS
  BUS --> SBX
  BUS --> FS
  SBX --> FS
  API -.->|"low-confidence plan only"| LLM
```

## 2. Components

```mermaid
flowchart TB
  L["Launcher (python main.py)<br/>src/launcher.py"]
  subgraph client["Client (src/ui)"]
    WB["WorkbenchView + five-screen Tk shell<br/>workbench.py"]
    TH["Palette, fonts, ttk styles<br/>theme.py"]
    HC["WorkbenchClient (urllib)<br/>api_client.py"]
  end
  subgraph app["API process (src/app)"]
    MW["Middleware<br/>limits.py, logging.py"]
    AUTH["Auth + RBAC<br/>auth.py, deps.py, roles.py"]
    ROUTES["Routers<br/>workflows · audit · approvals · policy · memory · admin"]
    STORES["Stores<br/>models.py, repositories.py, db.py"]
  end
  subgraph runtime["Runtime"]
    SCHED["Scheduler<br/>engine/executor/scheduler.py"]
    GATE["Gate adapter<br/>engine/executor/gate.py"]
    POLICY["PolicyService<br/>safety/policy_service.py"]
    PLAN["Planner<br/>orchestration/*"]
    DISP["Dispatcher<br/>bus/workers/dispatcher.py"]
    WSH["shell worker -> sandbox"]
    WAP["approval worker"]
    WCU["custom worker -> sandbox"]
    REPLAY["Replay<br/>app/replay.py"]
  end
  subgraph durable["Durable state (SQLite, one file)"]
    T1["users · roles · workspaces"]
    T2["workflows · workflow_versions"]
    T3["executions · execution_steps"]
    T4["approval_requests · approvals"]
    T5["memory_items · memory_events"]
    T6["audit_events (hash chain)"]
  end
  L -->|"health probe, then open"| WB
  L -->|"auto-login"| HC
  L -.->|"starts uvicorn only when /health is silent"| MW
  WB --> TH
  WB --> HC --> MW --> AUTH --> ROUTES --> STORES --> durable
  ROUTES --> PLAN
  ROUTES --> REPLAY
  SCHED --> GATE --> POLICY
  SCHED --> DISP
  DISP --> WSH
  DISP --> WAP
  DISP --> WCU
  DISP --> STORES
  PUB["event_logger.py"] --> T6
  STORES --> PUB
  WSH --> PUB
  WCU --> PUB
```

## 3. Deployment view

```mermaid
flowchart LR
  subgraph host["Docker host (compose.yml / compose.prod.yml)"]
    subgraph net["compose network (prod: nothing published but the API on loopback)"]
      A["api container<br/>uid 10001, PYTHONPATH=/code/src"]
      D[("postgres/pgvector<br/>reserved, not yet the app store")]
      R[("redis streams")]
    end
    V[("apidata volume<br/>/code/data/app.db")]
  end
  U["client / curl"] -->|":8000"| A
  A --- V
  A --> D
  A --> R
```

## 4. Sequence: run a planned workflow, pause for approval, replay

```mermaid
sequenceDiagram
  actor O as Operator
  actor AP as Approver
  participant API as API (app)
  participant P as Planner (orchestration)
  participant V as Validator/Policy (engine, safety)
  participant S as Scheduler (engine/executor)
  participant B as Bus + workers (bus)
  participant X as Sandbox (sandbox)
  participant A as Audit chain (storage)

  O->>API: POST /workflows/dry-run
  API->>V: load + validate + per-node verdict (inert)
  V-->>O: findings, verdicts, safe_to_run (persisted=false)
  O->>API: POST /workflows {dsl}
  API->>V: load + validate (same findings shape)
  API->>API: publish immutable (workflow_id, version)
  API-->>O: 200 {workflow_id, name, version, valid}
  O->>API: POST /executions {workflow_id, version}
  API->>API: pin (workflow_id, version) and insert RUNNING execution
  API-->>O: 202 {execution_id, status: RUNNING}
  Note over API,S: one background thread with its own loop<br/>and an in-process bus (src/app/run_service.py)
  API->>S: run DAG from the pinned dsl_text
  S->>V: gate each node (ALLOW / NEED_APPROVAL / DENY)
  S->>A: policy.decision
  alt NEED_APPROVAL
    S->>B: approval worker parks the node
    A->>A: approval request (quorum, deadline)
    AP->>API: POST /approvals/{run}/{node}/decisions
    API->>A: approval.decision (+ chain hash)
  end
  S->>B: step.dispatched
  B->>B: persist step state BEFORE ack
  B->>X: custom node only: sandboxed plugin run
  X-->>B: verdict + file diff metadata (or denial)
  B->>A: node.result
  S-->>API: RunResult
  API->>A: run.finished
  API->>API: record_result: steps + terminal status
  O->>API: GET /executions/{id} / {id}/timeline (poll)
  O->>API: POST /executions/{id}/replay (or GET .../audit/verify)
  API->>V: re-execute the pinned version row
  API->>A: run.replay (match/missing/unexpected)
```

### 4.1 Single-command startup (`python main.py`, item [105])

```mermaid
sequenceDiagram
  actor O as Operator
  participant M as main.py -> launcher
  participant U as uvicorn (same process)
  participant W as Workbench (Tk)

  O->>M: python main.py [--port N]
  M->>U: GET /health (1 s timeout)
  alt already healthy
    M->>M: reuse the running API
  else silent
    M->>U: start in a daemon thread, wait for /health (15 s)
  end
  M->>U: POST /auth/login (APP_ADMIN_USER/PASSWORD)
  U-->>M: bearer token (or a visible login prompt)
  M->>W: run_workbench(url, token)
  O->>W: validate -> dry run -> save -> run
  O->>M: close the window
  M->>U: stop the server if this process started it
```

On a headless host (no `DISPLAY`) the launcher stops after the API is
healthy and prints its URL; `--server-only`, `--no-server`, `--port N` and
`--chat` cover the other entry modes.

### 4.2 Workbench screens (item [110])

| Screen | Reads | Writes |
|---|---|---|
| Overview | `GET /metrics/executions`, `/executions`, `/approvals`, `/auth/me` | — |
| Workflow | `GET /workflows`, `/workflows/{id}/versions/{v}`, `POST /workflows/validate`, `/workflows/dry-run` | `POST /workflows` |
| Runs | `GET /executions`, `/executions/{id}/timeline` | `POST /executions`, `/executions/{id}/replay` |
| Approvals | `GET /approvals` | `POST /approvals/{run}/{node}/decisions` |
| Audit | `GET /executions/{id}/audit`, `/audit/verify` | — |

The Tk layer holds no rules: it renders the view model's dict rows and calls
one endpoint per action through `WorkbenchClient`. A single error boundary
turns `ApiError` into a status line and dialog, the header distinguishes an
unreachable API from a refused request, and the Runs screen polls every 2.5 s
while any execution is still `RUNNING`. `src/ui/theme.py` owns the palette,
font stack and ttk styles; the screen classes only name styles.

## 5. State machines

### 5.1 Node/step lifecycle (`src/engine/executor/states.py`)

```mermaid
stateDiagram-v2
  [*] --> PENDING
  PENDING --> READY
  PENDING --> BLOCKED
  READY --> RUNNING
  READY --> BLOCKED
  RUNNING --> SUCCEEDED
  RUNNING --> RETRYING
  RUNNING --> FAILED
  RUNNING --> COMPENSATING
  RETRYING --> RUNNING
  RETRYING --> FAILED
  FAILED --> COMPENSATING
  COMPENSATING --> COMPENSATED
  SUCCEEDED --> [*]
  BLOCKED --> [*]
  COMPENSATED --> [*]
```

### 5.2 Approval request (`src/app/approvals.py`)

```mermaid
stateDiagram-v2
  [*] --> PENDING: request(quorum, ttl)
  PENDING --> PENDING: decision below quorum
  PENDING --> APPROVED: distinct approvers >= quorum
  PENDING --> REJECTED: any rejection
  PENDING --> EXPIRED: deadline passes (decide or sweep)
  PENDING --> CANCELLED: run aborted
  APPROVED --> [*]
  REJECTED --> [*]
  EXPIRED --> [*]
  CANCELLED --> [*]
```

### 5.3 Memory item (`src/memory/states.py`)

```mermaid
stateDiagram-v2
  [*] --> QUARANTINED: stored (consent + PII scrub)
  QUARANTINED --> SANDBOX: successful sandbox/proof episode
  SANDBOX --> PUBLIC: reuse evidence + rating
  PUBLIC --> QUARANTINED: failure recorded
  PUBLIC --> [*]: erasure
```

## 6. Data model (SQLite, migrations 001–007)

```mermaid
erDiagram
  users ||--o| user_roles : "has role"
  users ||--o{ workspace_members : "member of"
  workspaces ||--o{ workspace_members : "includes"
  workspaces ||--o{ workflows : "owns"
  workflows ||--o{ workflow_versions : "versioned by"
  workflow_versions ||--o{ executions : "pinned by (workflow_id, version)"
  executions ||--o{ execution_steps : "per node attempt"
  executions ||--o{ approval_requests : "gates"
  approval_requests ||--o{ approvals : "decisions"
  executions ||--o{ audit_events : "hash chain (seq, prev_hash, hash)"
  memory_items ||--o{ memory_events : "lifecycle"
  users ||--o{ memory_items : "consents / owns"
```

## 7. Layer rules the code keeps

| Layer | Directory | May depend on | Must not |
|---|---|---|---|
| Client | `src/ui`, `src/launcher.py`, `main.py` | HTTP API only (the launcher may host uvicorn in-process) | import engine/safety/bus logic; hold business rules |
| API | `src/app` | engine, safety, storage, bus, memory, orchestration | execute OS commands itself |
| Engine | `src/engine` | safety, storage | touch the network or the client |
| Safety | `src/safety` | pure data | call the executor (policy decides, it does not run) |
| Bus/workers | `src/bus` | engine gate, sandbox, storage | trust a producer's verdict; skip durable state before ack; run a command outside the sandbox |
| Sandbox | `src/sandbox` | stdlib only | import app code; run without isolation |
| Storage | `src/storage` | stdlib, sqlite | import UI or engine |

`docs/THREAT_MODEL.md` analyses the trust boundaries these rules create, and
`tests/unit/test_threat_model_doc.py` fails if a cited file moves.

## 8. Reading order for a reviewer

1. `docs/ARCHITECTURE.md` (this file) — the shape.
2. `docs/TRACEABILITY.md` — which requirement is proven where.
3. `docs/ADR.md` + `docs/RISK_REGISTER.md` — why it is shaped this way, and
   what is still exposed.
4. `docs/SECURITY_REPORT.md` + `docs/THREAT_MODEL.md` — the risk story.
5. `docs/DEPLOYMENT.md` + `docs/PERF_REPORT.md` — how it runs and how fast.
6. `docs/LIMITATIONS.md` — what it deliberately does not do.
