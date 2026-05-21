# Lessons Learned

- Cost price must remain optional, but holding-specific sell rules should only execute when it is provided. Without it, the strategy card should show the missing context instead of inventing position-risk conclusions.
- Reviewer agents should use absolute report paths when the project sits inside a parent workspace; relative `docs/...` paths can land outside the target project.
- For recommendation systems, distinguish "no candidate passed filters" from "upstream market data failed"; persist source outages as failed or explicitly degraded runs instead of normal fallback recommendations.
