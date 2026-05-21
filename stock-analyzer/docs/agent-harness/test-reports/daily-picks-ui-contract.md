# Daily Picks UI/Contract Review

## Result
PASS

## Issue Count
0

## Scope
Reviewed:
- `stock-analyzer/src/templates/index.html`
- `stock-analyzer/src/main.py`

## Findings
I did not find any contract or frontend wiring issues that block the daily picks page.

### Route and navigation
- `#/daily-picks` is mapped in the client router and `handleHashChange()` resolves it to `daily-picks`.
- `navigateToPage()` shows/hides `page-daily-picks` alongside `analyze` and `watchlist` without interfering with either page.
- The nav links for desktop and mobile both point at `#/daily-picks` and call `switchPage(...)`, so the route is reachable from both layouts.

### Backend contract
- `main.py` exposes `GET /api/daily-picks` and `POST /api/daily-picks/run`.
- `POST /api/daily-picks/run` accepts `strategy` values in `STRATEGY_KEYS` or `all`, which matches the client usage.
- The daily picks response shape from `models.get_daily_picks()` is grouped by `strategy_key`, which matches the client renderer.

### Group rendering
- The page renders from `DAILY_PICK_STRATEGIES` and maps each strategy key to its own group, so the three strategy families stay separated.
- I did not see any merged ranking or single-list rendering path for daily picks.

### Recommendation card contract
- Each recommendation card renders:
  - code
  - name
  - rank
  - score
  - mode
  - reasons
  - risks
  - metrics
  - data warning/status via `formatDailyDataStatus(...)`
- The layout uses wrapping/truncation helpers on the denser fields, so the card content is reasonably guarded against overflow on mobile and desktop.

### Refresh behavior
- The “刷新全部” button calls `runDailyPicks('all')`, which sends `POST /api/daily-picks/run`.
- After a successful run, the client renders the returned groups directly when available, otherwise it falls back to reloading `GET /api/daily-picks`.

### Script health
- The inline script parses successfully.
- I did not find duplicate `function` declarations in the template script.
- The helper names used by the daily-picks flow are defined in the same script.

## Notes
- I did not run the app in a browser for visual QA in this review; this was a static contract check only.
