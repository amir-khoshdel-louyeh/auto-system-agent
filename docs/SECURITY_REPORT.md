# Security and dependency report

**Item:** Phase 9 — [82] Run Bandit, dependency audit and secret scan
**Run:** 2026-10-18, `local_commit` @ `0f3fd8f` + the [82] commit
**Environment:** dev machine — Python 3.14.8, Bandit 1.9.4, pip-audit 2.10.1

Every number below is copied from the command output, not estimated. The
commands are re-runnable as written and also run in CI
(`.github/workflows/ci.yml`, job `security`).

---

## 1. Static analysis — Bandit

```bash
bandit -r src -ll -ii          # medium+ severity, medium+ confidence
```

| Metric | Result |
|---|---|
| Lines of code scanned | 18 401 |
| **High severity** | **0** |
| **Medium severity** | **0** |
| Low severity (informational, below the gate) | 124 |
| `# nosec` suppressions | 10 audited annotations (listed below) |

Gate: medium+ must be zero — **pass**.

### 1.1 Suppressions audited in this phase

| Location | Test | Why it is safe | Review |
|---|---|---|---|
| `src/sandbox/runner.py` (`--tmpfs /tmp` in the bwrap argv) | B108 hardcoded tmp directory | It *names* a mount point inside the sandbox's own mount namespace; the plugin never sees the host `/tmp`, and the sandbox's own `TMPDIR` points at the workspace. | re-reviewed [82] — kept with an inline comment |
| `src/sandbox/runner.py`, `src/bus/workers/*`, `src/app/*` (subprocess/urllib) | B603/B404/B310 | Fixed argv, no shell, and URLs built from configuration; each is annotated with its reason at the call site. | re-reviewed [82] — kept |
| `src/sandbox/runner.py` `BWRAP_PROBE_WORKSPACE` | B108 | **Removed** in this phase: the constant was dead code (only re-exported), so the finding was fixed rather than suppressed. | [82] fixed |

The remaining 124 low findings are the usual informational set (`assert`
usage in tests-adjacent code, `try/except/pass` in teardown paths) and are
below the configured gate; they are listed by `bandit -r src -l` if needed.

## 2. Dependency audit — pip-audit

```bash
pip-audit -r requirements.txt        # core runtime
pip-audit -r requirements-api.txt    # API image dependencies
pip-audit -r requirements-dev.txt    # tooling (bandit, pip-audit, pytest, mypy)
```

| Requirements file | Known vulnerabilities |
|---|---|
| `requirements.txt` | **none found** |
| `requirements-api.txt` | **none found** |
| `requirements-dev.txt` | **none found** |

Gate: no known vulnerable dependency in any of the three sets — **pass**.

## 3. Secret scan

```bash
python scripts/secret_scan.py
# secret scan: clean (255 files, 9 rules)
```

Rules: private-key blocks, AWS access keys, GitHub tokens, Slack tokens,
Google API keys, Stripe live secrets, OpenAI-style keys, JWTs, and the
generic `secret_key = "<24+ chars>"` assignment (source paths only, so the
deliberate fixture passwords in `tests/` do not drown the signal).

Two fixture hits existed and were reviewed rather than hidden: a dummy JWT in
`tests/unit/test_log_redaction.py` and the AWS *documentation example* key in
`tests/unit/test_memory_scrub.py`. Both lines carry an explicit
`# secret-scan: allow` marker with the reason, so suppression shows up in
review and the scanner still fails on any unmarked credential. The scan is
also asserted inside the suite
(`tests/unit/test_secret_scan.py::RepositoryTests::test_the_repository_has_no_findings`),
so a committed secret breaks the build, not just CI.

## 4. What this report does *not* claim

- Bandit and pip-audit check code patterns and published advisories; they do
  not prove the absence of vulnerabilities. The design-level analysis is
  `docs/THREAT_MODEL.md`.
- The dependency sets are range-pinned (`>=x,<y`) and installed at image
  build time, so a fresh advisory after this run is not retroactively
  covered — CI re-runs the audit on every push.
- No dynamic/penetration testing was performed (out of MVP scope; see
  `docs/LIMITATIONS.md`).

## 5. Reproducing

```bash
bandit -r src -ll -ii
pip-audit -r requirements-api.txt
python scripts/secret_scan.py
python -m pytest tests/unit/test_secret_scan.py -q
```

The exported machine-readable copies live in `docs/reports/` (Phase 10
item [93]).
