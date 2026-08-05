# Architecture (Phase 10 item [90])

Diagrams are Mermaid so they render in the repository view and can be diffed
like code. Every box names the module that implements it; every state machine
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
  subgraph client["Client (src/ui)"]
    WB["WorkbenchView + Tkinter tree<br/>workbench.py"]
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
    WSH["shell worker"]
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
  O->>API: publish/run intent
  API->>P: plan (offline rules first, LLM only if configured)
  P->>V: compile_plan through the same loader/validator/policy path
  API->>API: pin (workflow_id, version) and insert RUNNING execution
  API->>S: run DAG
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
  O->>API: POST replay (or GET /executions/{id}/audit/verify)
  API->>V: re-execute the pinned version row
  API->>A: run.replay (match/missing/unexpected)
```

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
| Client | `src/ui`, `main.py` | HTTP API only | import engine/safety/bus logic; hold business rules |
| API | `src/app` | engine, safety, storage, bus, memory, orchestration | execute OS commands itself |
| Engine | `src/engine` | safety, storage | touch the network or the client |
| Safety | `src/safety` | pure data | call the executor (policy decides, it does not run) |
| Bus/workers | `src/bus` | engine gate, sandbox, storage | trust a producer's verdict; skip durable state before ack |
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
