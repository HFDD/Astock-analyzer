# Frontend UI Spec Review

Status: PASS
Issue count: 0

Inspected file: `src/templates/index.html`

## Checklist

- PASS: Search area contains stock code input, buy trade date select, buy price/cost input, and analyze button.
- PASS: Buy trade date is a disabled select before loading, populated from `/api/stock/{code}/trade-dates`, and is not a free date input.
- PASS: `analyzeStock` uses `URLSearchParams` and sends `buy_date` and `cost_price` when present.
- PASS: Top analysis summary prefers `summary_text` and falls back to an extracted short summary from `analysis_text`.
- PASS: Bottom comprehensive report renders the full `analysis_text`.
- PASS: Strategy card prefers `strategy_exit.strategies` and renders multiple cards with name, risk level, decision, summary, and signals.
- PASS: Legacy `strategy_exit.signals` fallback remains.
- PASS: Strategy position line includes buy trade date, T+1 state, cost, current profit, and minute data status.
- PASS: Top recommendation label is `综合建议`.
- PASS: Technical action card shows `technical_action` plus basis/reason text covering half-position source and strategy adjustment.
- PASS: Kline/MACD/KDJ/RSI/BOLL chart DOM blocks appear after `最近分析记录` and retain their IDs.
- PASS: `120日` is initially highlighted while `state.klinePeriod` defaults to `120`.
