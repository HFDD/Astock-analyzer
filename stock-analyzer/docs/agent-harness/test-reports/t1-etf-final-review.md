# T1 ETF Final Review

Result: PASS
Issue count: 0

Scope inspected:
- `src/templates/index.html`
- `src/analyzer.py`
- `src/data_fetcher.py`
- `tests/test_strategy_exit.py`
- Supporting route check in `src/main.py` for the trade-date API and query propagation.

## Checklist

| Requested behavior | Status | Evidence |
| --- | --- | --- |
| Watchlist Analyze jumps to analyze tab and starts analysis. | PASS | `analyzeFromWatchlist` sets `window.location.hash` to `#/analyze`, calls `navigateToPage('analyze')`, fills `stockCodeInput`, triggers `handleStockCodeInput()`, then calls `analyzeStock(normalizedCode)`. |
| Buy trade date uses year/month/day selector limited to valid trading dates. | PASS | The analyze form uses `buyYearSelect`, `buyMonthSelect`, and `buyDaySelect`. `loadBuyTradeDates` fetches `/api/stock/{code}/trade-dates`; `populateBuyDateControls`, `updateBuyMonthOptions`, and `updateBuyDayOptions` derive selectable years/months/days only from fetched valid dates. `getSelectedBuyDate` only returns a date present in `state.tradeDateSet`. |
| T+1 is not shown in strategy exit prompts/card/status line. | PASS | Backend strategy keys exclude `t_plus_one`; tests assert no `t_plus_one`, `T+1`, or `T＋1` appears in strategy structures. Frontend filters any legacy `t_plus_one` strategy entry and renders same-day status as Chinese `下一交易日卖出预案`. |
| Same-day buy displays next-trading-day sell guidance and final action is not executable sell. | PASS | `_build_trade_eligibility` sets `can_sell_today=False` and `guidance_scope='next_trading_day'` when `buy_date` equals latest trade date. `analyze_stock` converts a same-day sell recommendation to `观望`; `_align_action_with_recommendation` sets `operation='观望'` and `position='原仓观察'`. Tests cover this behavior. |
| ETF does not participate in individual-stock strategy exit prompts. | PASS | `classify_instrument` detects ETF/LOF/fund instruments. `analyze_stock` routes ETFs to `_build_etf_strategy_exit`, which returns no strategy cards/signals and summary text `ETF 不参与个股卖点策略`. Frontend renders an ETF-specific explanation instead of individual-stock strategy cards when `is_etf`/`instrument_type` indicates ETF. |
| Strategy decisions display Chinese. | PASS | `EXIT_DECISION_LABELS` maps decision codes to Chinese labels; each strategy result includes `decision_label`. Frontend prefers `decision_label` and has a fallback mapping from English action keys to Chinese labels. Tests assert decision labels match the Chinese contract. |
| Each strategy card has expandable sell signals. | PASS | `renderStrategyExitCard` renders every strategy card with `renderSignalsDetails(strategySignals)`, which uses a `<details>` block and `卖点信号（N）` summary. |

## Issues

No issues found in the inspected scope.

## Verification

- Attempted `pytest -q tests/test_strategy_exit.py`: unavailable because `pytest` is not on `PATH`.
- Attempted `python -m pytest -q tests/test_strategy_exit.py`: unavailable because `python` is not on `PATH`.
- Attempted `python3 -m pytest -q tests/test_strategy_exit.py`: Python exists, but `pytest` is not installed in that environment.

