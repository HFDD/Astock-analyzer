# Position Strategy UI Harness Plan

| # | Task | Status | Dev | Review/Test | Notes |
|---|------|--------|-----|-------------|-------|
| 1 | Backend buy-date strategy API and tests | pass | Sartre | pass | Py compile/import/direct probes passed; pytest unavailable |
| 2 | Frontend buy-date selector, strategy cards, chart relocation | pass | Helmholtz | pass | Static template checks passed; browser smoke not run |
| 3 | Backend strategy spec review | pass | Pasteur | pass | 0 issues after API-level tests |
| 4 | Frontend UI spec review | pass | Rawls | pass | 0 issues |
| 5 | Integration and quality review | pass | Ramanujan | pass | 0 issues after frontend formatter repair |
| 6 | T+1 trade eligibility and ETF backend contract | pass | Chandrasekhar | pass | 14 backend tests passed |
| 7 | Watchlist jump, Y/M/D trade-date selector, strategy expand UI | pass | Aquinas | pass | Node static checks passed |
| 8 | Contract/UI regression review | pass | Popper | pass | 0 issues; report in test-reports/t1-etf-final-review.md |

## Three Strategy Daily Picks

| # | Task | Status | Dev | Review/Test | Notes |
|---|------|--------|-----|-------------|-------|
| 1 | Red tests for daily picks strategy/persistence/API | pass | Orchestrator | pass | `uv run ... pytest -q` passed 24 tests; system `python3` still lacks pytest |
| 2 | Backend strategy engine, data provider, runner, DB, API | pass | Orchestrator | pass | Strategy engine, runner, SQLite persistence, and API implemented |
| 3 | Frontend 今日推荐 page | pass | Confucius | pass | Navigation, grouped page, refresh flow, and card rendering implemented |
| 4 | Strategy fidelity review | pass | Hubble | pass | Initial FAIL repaired; re-review PASS, 0 issues |
| 5 | Backend/API/integration review | pass | Copernicus | pass | Initial ETF outage FAIL repaired; re-review PASS, 0 issues |
| 6 | Final verification and repair loop | pass | Orchestrator | pass | AST/py_compile/Node parse/pytest/API smoke checks passed |
