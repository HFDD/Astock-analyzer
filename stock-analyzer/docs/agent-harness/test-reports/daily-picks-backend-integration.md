# Daily Picks Backend/API/Integration Review

Result: FAIL

Issue count: 1

Scope reviewed:
- `stock-analyzer/src/models.py`
- `stock-analyzer/src/schema.sql`
- `stock-analyzer/src/daily_picks.py`
- `stock-analyzer/src/daily_picks_runner.py`
- `stock-analyzer/src/main.py`
- `stock-analyzer/tests/test_daily_picks.py`

## Findings

### P1: ETF data-source failures are silently converted into a defensive pick instead of a failed/degraded run

File: `stock-analyzer/src/daily_picks.py`

Lines:
- `AkshareDailyPickProvider.etf_history`: lines 259-289
- `AkshareDailyPickProvider.etf_spot_map`: lines 291-304
- `AkshareDailyPickProvider.a_share_weak_state`: lines 306-329
- `EtfRotationStrategy.run`: lines 729-760

Problem:

The stock strategies preserve call-auction and sentiment source failures in `data_status.warning`, but ETF source failures do not get equivalent warning propagation. `etf_history()` catches both AKShare ETF/LOF history exceptions and returns an empty DataFrame. `etf_spot_map()` returns `{}` when spot data is empty. `a_share_weak_state()` swallows every index-source exception and returns counts/details without a warning. `EtfRotationStrategy.evaluate()` then drops every ETF with missing/empty history, and `run()` emits a normal `success` result with one defensive recommendation:

```text
data_status.warning = "无ETF通过动量过滤，展示防御ETF可用性"
```

That warning does not distinguish a valid "no ETF passed filters" market condition from a total data-source outage. The persisted run also remains `status = "success"` and `error = None`, so API consumers cannot tell whether the ETF rotation result is meaningful or just a fallback caused by missing AKShare data.

Why it matters:

The daily-picks module-level contract explicitly says source failures should enter `data_status` warning instead of treating missing auction/ETF data as valid signal. For ETF rotation, a provider outage currently produces an apparently successful recommendation set. That can mislead `/api/daily-picks`, the frontend, and any cron/automation reader.

Suggested fix:

Return structured warning/status information from ETF source methods, or have the provider raise typed data-source errors that `EtfRotationStrategy.run()` can catch and persist as a failed/degraded strategy run. At minimum, when ETF spot/history/index sources are empty due to exceptions, include a per-run or per-recommendation `data_status.warning` that says the ETF data source was unavailable, not merely that no ETF passed the filters.

Test gap:

`tests/test_daily_picks.py` covers call-auction warnings and persistence shape, but it does not cover ETF provider failures. Add a fake ETF provider whose `etf_history`, `etf_spot_map`, and/or `a_share_weak_state` fail or return empty data because of simulated upstream errors, then assert the run/API response exposes the source failure distinctly through `status`, `error`, or `data_status.warning`.

## Checks

Schema/model idempotence:

PASS. `strategy_runs` has `UNIQUE(strategy_key, trade_date)`, `save_strategy_run_results()` updates an existing run and deletes/reinserts child recommendations, and the focused test verifies idempotent replacement.

JSON handling/result shape:

PASS with one caveat covered by the issue above. Recommendation JSON fields are serialized with `ensure_ascii=False`, loaded with defaults, and returned as objects/lists in `get_daily_picks()`.

API auth/response shape:

PASS. `GET /api/daily-picks` and `POST /api/daily-picks/run` both require `Depends(get_current_user)` and return the project-standard `{"data": ..., "message": ...}` success wrapper. Invalid strategy returns the project-standard error wrapper.

Runner:

PASS. `python -m src.daily_picks_runner --help` works under `uv run` from the project root and exposes `first_board_relay`, `leader_chase`, `etf_rotation`, and `all`.

Import compatibility:

PASS. Top-level `src` path imports and package-mode imports both succeeded in an isolated `uv run` environment with project dependencies installed.

Obvious crashes/route conflicts:

PASS. No route conflict found for `/api/daily-picks` or `/api/daily-picks/run`; FastAPI declares those concrete routes before `/api/stock/{code}` and under a different prefix.

Brittle tests:

PASS with dependency note. The focused daily-picks test file passes when `httpx` is present for Starlette's `TestClient`. `httpx` is not listed in `requirements.txt`, so a fresh environment that only installs runtime requirements cannot collect the tests.

## Verification

Commands run:

```bash
uv run --with pytest --with httpx --with fastapi --with pandas --with numpy --with uvicorn --with 'python-jose[cryptography]' --with 'passlib[bcrypt]' --with python-multipart --with jinja2 --with aiofiles --with akshare python -m pytest tests/test_daily_picks.py -q
```

Result:

```text
9 passed, 3 warnings in 46.24s
```

```bash
uv run --with pandas --with numpy --with akshare python -m src.daily_picks_runner --help
```

Result: PASS.

## Re-review: ETF Data-source Outage Handling

Result: PASS

Issue count: 0

Re-reviewed only the previous FAIL item: ETF data-source outages being silently converted into defensive picks.

Resolution verified:

- `DataSourceUnavailable` is defined in `src/daily_picks.py`.
- `AkshareDailyPickProvider.etf_spot_map()` now raises `DataSourceUnavailable` when the ETF spot source raises or returns empty data.
- `AkshareDailyPickProvider.etf_history()` now raises `DataSourceUnavailable` when both ETF and LOF historical source calls fail.
- `EtfRotationStrategy.run()` catches `DataSourceUnavailable` during ETF spot/universe build and returns `StrategyRunResult(status="failed", total_scanned=0, recommendations=[], error=...)`.
- `DailyPickService.run_strategy()` persists failed strategy results with `total_picks = 0` because it saves the returned failed `StrategyRunResult`, and also catches any later strategy exception as failed.
- `tests/test_daily_picks.py` includes `test_etf_rotation_marks_source_outage_as_failed_run`, covering the prior silent defensive-pick failure path.

No remaining finding for the previously reported outage case. A true ETF spot outage is no longer represented as a normal successful defensive recommendation.

Fresh verification supplied by orchestrator:

```text
uv run --with pytest --with httpx --with fastapi --with pandas --with numpy --with uvicorn --with 'python-jose[cryptography]' --with 'passlib[bcrypt]' --with python-multipart --with jinja2 --with aiofiles --with akshare python -m pytest -q
=> 24 passed, 3 warnings
```

Additional supplied smoke checks:

- `EtfRotationStrategy` with `etf_spot_map` raising `DataSourceUnavailable` returned `failed` with `0` recommendations.
- `DailyPickService` persisted ETF outage as `failed` with `total_picks = 0`.

```bash
uv run --with fastapi --with pandas --with numpy --with uvicorn --with 'python-jose[cryptography]' --with 'passlib[bcrypt]' --with python-multipart --with jinja2 --with aiofiles --with akshare python - <<'PY'
import sys
sys.path.insert(0, 'src')
import daily_picks, daily_picks_runner, main, models
print('top-level imports ok')
PY
```

Result: PASS.

```bash
uv run --with fastapi --with pandas --with numpy --with uvicorn --with 'python-jose[cryptography]' --with 'passlib[bcrypt]' --with python-multipart --with jinja2 --with aiofiles --with akshare python - <<'PY'
import src.daily_picks, src.daily_picks_runner, src.main, src.models
print('package imports ok')
PY
```

Result: PASS.
