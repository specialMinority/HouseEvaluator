# HouseEvaluator v2 development and deployment

The supplier-free personal comparison preview is deployed at https://houseevaluator-personal.onrender.com.
Detailed local acceptance logs and user-entered rental examples are not published.

- [x] Public listing search foundation, bounded discovery, manual input and building-aware comparison.
- [x] Closest-listing price chart, observed sample rank, condition differences and source links.
- [x] Render Free Docker deployment with access-code protection and manual deploys.
- [x] Local regression: 747 passed, 1 skipped, 401 subtests passed; isolated Docker smoke passed.
- [x] GitHub CI: Python 3.12, Python 3.14 and browser JavaScript syntax passed.
- [x] Live HTTPS, API authorization, synthetic comparison and desktop UI verified.
- [x] Live mobile UI: 23/23 checks passed at 390 by 844, including charts, source links and logout.
- [x] One actual cloud search was attempted and its failure was recorded honestly.
- [ ] Cloud automatic-search availability: SUUMO returned HTTP 503 and no listings during the first check. Manual comparison works; automatic search is not verified as operational.

Deployment source: codex/houseevaluator-v2 at fe884643227ab675e8628bfe060cca2c5bd5a2dc.
The main branch is unchanged. See docs/DEPLOY_RENDER.md for actual results and operating procedures.
No local search captures, databases, access codes, or account verification data are included.
