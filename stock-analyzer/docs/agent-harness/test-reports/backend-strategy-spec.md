# Backend Strategy Spec Review

Result: PASS
Issue count: 0

Scope inspected:
- `src/main.py`
- `src/analyzer.py`
- `tests/test_strategy_exit.py`

## Checklist

| Spec item | Status | Evidence |
| --- | --- | --- |
| API accepts `buy_date` and `cost_price`. | PASS | `analyze_stock_api` accepts both query params and passes them into `analyze_stock`; `test_stock_api_passes_buy_date_and_cost_price_to_analyze_stock` asserts the route forwards `cost_price=9.75` and `buy_date=2026-01-02`. |
| `GET /api/stock/{code}/trade-dates` exists, requires auth, returns recent trade dates, and does not deduct credits. | PASS | Route exists with `Depends(get_current_user)`, returns `trade_dates`/`latest_trade_date`, and has no `deduct_credits` call. `test_trade_dates_api_requires_auth_and_does_not_deduct_credits` covers unauthenticated 401, authenticated success, returned dates, and zero credit deduction. |
| `buy_date` is validated against current K-line trading dates; invalid date has clear `ValueError`. | PASS | `_validate_buy_date` normalizes `YYYY-MM-DD`, checks against K-line dates, and raises `ValueError("buy_date ... is not a trading date in current data")`; test covers invalid date error text. |
| T+1 blocked can coexist with other stop-loss/take-profit/sell risk warnings. | PASS | T+1 blocked signal is non-executable and does not suppress other signals; test covers T+1 plus hard stop-loss warning. |
| `strategy_exit.strategies` exists with stable keys: `position_risk`, `t_plus_one`, `first_board_relay`, `leader_chase`, `trend_break`, `rotation_quality`. | PASS | `STRATEGY_EXIT_DEFINITIONS` defines all keys and `_build_strategy_results` emits a result for each key; tests assert exact key set. |
| Strategy results are frontend-displayable with `key`/`name`/`risk_level`/`risk_score`/`decision`/`summary`/`signals`. | PASS | `_build_strategy_result` returns all required fields; tests assert the display contract for every strategy. |
| Old `strategy_exit.signals` compatibility is preserved. | PASS | `analyze_strategy_exit` still returns top-level `signals`, and tests assert signal compatibility fields. |
| `action` no longer obviously conflicts with final recommendation; `technical_action` is preserved and `action` has `basis`/`reason`. | PASS | `_align_action_with_recommendation` aligns operation/position to final recommendation, adds `basis` and `reason`, and result preserves `technical_action`. |
| Tests cover required scenarios from the plan. | PASS | The previous gap is repaired: `tests/test_strategy_exit.py` now includes API-level tests for query propagation and `/trade-dates` auth/no-credit/response contract, alongside analyzer strategy tests. |

## Issues

No issues found in the inspected scope.

