# Daily Picks Strategy Fidelity Review

Result: FAIL
Issue count: 3

Scope inspected:
- `stock-analyzer/src/daily_picks.py`
- `stock-analyzer/tests/test_daily_picks.py`
- `stock-analyzer/docs/agent-harness/plan.md`

Verification:
- Attempted `python -m pytest stock-analyzer/tests/test_daily_picks.py -q`: `python` is not on PATH.
- Attempted `python3 -m pytest stock-analyzer/tests/test_daily_picks.py -q`: Python exists, but `pytest` is not installed in this environment.

## Findings

### 1. First-board market-cap filters are bypassed when cap data is missing

Severity: High

Evidence:
- `FirstBoardRelayStrategy.evaluate` reads `market_cap` and `float_market_cap`, but only enforces the bounds when each field is not `None`.
- Lines 378-383:
  - `market_cap is not None and market_cap < 3_000_000_000`
  - `float_cap is not None and float_cap > 30_000_000_000`

Why this fails the spec:
- The spec requires `market cap >=30e8 and float <=300e8`.
- With the current implementation, a candidate with missing market-cap or float-cap data can still pass every downstream filter and be recommended.
- That makes a required universe filter data-dependent instead of mandatory.

Expected behavior:
- Missing `market_cap` or `float_market_cap` should fail the candidate or produce an explicit unavailable-data warning state that does not treat the candidate as a valid filtered pick.

Suggested test coverage:
- Add a first-board candidate with otherwise passing history and auction data but missing `market_cap`.
- Add a candidate missing `float_market_cap`.
- Assert neither becomes a normal `首板接力` recommendation.

### 2. Leader-chase auction-volume filter is bypassed when previous volume is zero or unavailable

Severity: High

Evidence:
- `LeaderChaseStrategy.evaluate` computes `prev_vol` from the last 5-day history row, falling back to candidate volume.
- The auction-volume threshold is enforced only when `prev_vol > 0`.
- Lines 500-509:
  - `prev_vol = ... or 0`
  - `if prev_vol > 0 and auction_vol / prev_vol < 0.01: return None`
- Later, a pick can be returned with `auction_volume_ratio` set to `None`.

Why this fails the spec:
- The spec requires `auction volume >=1%`.
- If previous volume is zero or unavailable, the implementation does not reject the candidate. It allows the recommendation to proceed through mode selection and scoring without proving the required 1% auction-volume condition.

Expected behavior:
- If previous volume is missing or non-positive, the strategy should reject the candidate or return a warning/non-pick state rather than allowing a normal recommendation.

Suggested test coverage:
- Add a leader candidate with passing open percentage and mode conditions but `prev_vol == 0`.
- Assert it is not returned as a normal `龙追`, `高开`, or `低开反弹` pick.

### 3. Test coverage does not lock down the ETF rotation contract

Severity: Medium

Evidence:
- `tests/test_daily_picks.py` has direct strategy tests for:
  - first-board own pool and auction-volume ratio.
  - leader-chase 09:25 cutoff and modes.
  - momentum-score helper.
  - persistence and API reads.
- It does not instantiate or run `EtfRotationStrategy`.
- It does not assert:
  - ETF pool selection under weak/non-weak A-share states.
  - momentum and filter pass/fail behavior.
  - recommendation-only behavior.
  - `13:10` scheduled-time contract.

Why this matters:
- The implementation does include ETF pools, momentum filters, defensive fallback, and a risk string saying the result is recommendation-only and formally valid at 13:10.
- `STRATEGY_META` also carries `scheduled_time: "13:10"` for `etf_rotation` in `models.py`, but that metadata file is outside the requested inspection list and the daily-picks tests do not guard it.
- Because ETF behavior is one of the three named strategy contracts, the current tests do not provide enough regression protection for this portion of the spec.

Expected behavior:
- Add direct ETF strategy tests using a fake provider:
  - weak state uses the global ETF pool.
  - non-weak state uses the combined ETF pool.
  - passing momentum/filter inputs return only recommendation records, not executable trade actions.
  - no passing ETF returns `防御观察` with a warning.
  - grouped daily-picks metadata includes `scheduled_time == "13:10"` for `etf_rotation`.

## Fidelity Checklist

| Spec item | Status | Notes |
| --- | --- | --- |
| first_board_relay uses own first-board universe | PASS | `build_universe` calls `provider.first_board_candidates(previous, before_previous)`. |
| first_board_relay base exclusions | PASS | Excludes ChiNext/Beijing/other non-main-board prefixes and 688 in strategy; provider excludes ST/退 in limit-up source. |
| first_board_relay high volatility <=20% | PASS | Uses 5-day high/low range and rejects above 20%. |
| first_board_relay market cap >=30e8 and float <=300e8 | FAIL | Bounds are skipped when fields are missing. |
| first_board_relay amount 5-30e8 | PASS | Rejects outside range. |
| first_board_relay volume structure | PASS | Requires limit-up day volume at least 2x prior day and prior day not over 2x prior-prior day. |
| first_board_relay blow-off exclusion | PASS | Rejects extreme volume expansion into 30-day high breakout. |
| first_board_relay T-2/T-3 positive candles and <5% gains | PASS | Checks two prior candles are positive and their gains are below 5%. |
| first_board_relay auction 09:15-09:26 | PASS | Calls `call_auction(..., "09:15:00", "09:26:00")`. |
| first_board_relay auction volume >=3% and pct 0-6% | PASS with boundary caveat | Volume threshold is inclusive; pct is implemented as strictly greater than 0 and strictly less than 6%. |
| first_board_relay warning when auction unavailable | PASS | Returns `待确认` recommendation with `data_status.warning`. |
| leader_chase own yesterday limit-up universe | PASS | Uses `provider.limit_up_candidates(previous)`. |
| leader_chase sorted by continue count, top 15 | PASS | Sorts descending by `continue_count` and slices `[:15]`. |
| leader_chase sentiment bull/cautious/bear using CSI1000 | PASS | Provider uses index symbol `000852` and maps to `bull`, `cautious`, or `bear`. |
| leader_chase auction 09:15-09:25 only | PASS | Calls `call_auction(..., "09:15:00", "09:25:00")`; test asserts cutoff. |
| leader_chase near-limit filters | PASS | Rejects auction prices within 0.01 of high/low limit. |
| leader_chase auction volume >=1% | FAIL | Threshold is skipped when previous volume is missing or zero. |
| leader_chase modes 龙追/高开/低开反弹 | PASS | Implements all three modes. |
| leader_chase bear only low-open rebound | PASS | Bear state rejects any mode except `低开反弹`. |
| leader_chase max 5 picks | PASS | Slices picks to `[:5]`. |
| etf_rotation ETF pool and momentum/filters | PASS | Implementation contains global/China pools, weak-state selection, momentum, volume, loss, Laplace, MA/R² filters, and defensive fallback. |
| etf_rotation recommendation-only behavior | PASS with test gap | Risks state no auto-trading/recommendation-only behavior; no test locks this down. |
| etf_rotation 13:10 metadata/UI contract | PASS with test gap | Metadata exists in `models.py`, outside requested implementation file; daily-picks tests do not assert it. |

## Plan Alignment

`stock-analyzer/docs/agent-harness/plan.md` still marks “Strategy fidelity review” as pending for the three-strategy daily picks section. This report should keep that row failing until the required-filter bypasses and ETF coverage gap are repaired.

## Re-review

Result: PASS
Issue count: 0

Scope inspected:
- `stock-analyzer/src/daily_picks.py`
- `stock-analyzer/tests/test_daily_picks.py`
- `stock-analyzer/src/models.py` for `STRATEGY_META["etf_rotation"]["scheduled_time"]`

Verification:
- Attempted `python -m pytest stock-analyzer/tests/test_daily_picks.py -q`: `python` is not on PATH.
- Attempted `python3 -m pytest stock-analyzer/tests/test_daily_picks.py -q`: Python exists, but `pytest` is not installed in this environment.

### Previously Failed Item 1: first_board_relay market-cap filters

Status: PASS

Evidence:
- `FirstBoardRelayStrategy.evaluate` now rejects candidates when either `market_cap` or `float_market_cap` is missing before applying the numeric bounds.
- It then enforces `market_cap >= 3_000_000_000` and `float_market_cap <= 30_000_000_000`.
- Regression coverage exists in `test_first_board_requires_market_cap_context`, which supplies otherwise plausible candidates missing either total market cap or float market cap and asserts no recommendations are returned.

### Previously Failed Item 2: leader_chase auction-volume threshold with missing/zero previous volume

Status: PASS

Evidence:
- `LeaderChaseStrategy.evaluate` now rejects `prev_vol <= 0` before checking `auction_vol / prev_vol < 0.01`.
- This closes the prior bypass where candidates with missing or zero previous volume could proceed without proving the required 1% auction-volume condition.
- Regression coverage exists in `test_leader_chase_rejects_missing_previous_volume`, which sets the previous day volume to zero and asserts no recommendations are returned.

### Previously Failed Item 3: ETF rotation contract coverage

Status: PASS

Evidence:
- `test_etf_rotation_uses_global_pool_in_weak_state_and_is_recommendation_only` now directly instantiates `EtfRotationStrategy`, verifies weak-state global-pool scanning, asserts a recommendation from `GLOBAL_ETF_POOL`, asserts the recommendation-only/no-order risk text contains `不下单`, and asserts `STRATEGY_META["etf_rotation"]["scheduled_time"] == "13:10"`.
- `test_etf_rotation_falls_back_to_defensive_observation_when_no_etf_passes` now covers the defensive fallback path, including code `511880`, mode `防御观察`, and a warning in `data_status`.

### Re-review Conclusion

The three previously reported fidelity issues are resolved in the inspected code and covered by targeted tests. Residual verification risk is limited to the local environment lacking `pytest`, so the tests were inspected statically but not executed here.
