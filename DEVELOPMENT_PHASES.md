# HouseEvaluator v2 development and deployment

Public progress record. User-entered rental examples, source snapshots, access codes and detailed diagnostic evidence remain local.

## Current operating state — 2026-09-13

The existing public site, https://houseevaluator-personal.onrender.com, now completes actual URL autofill, automatic search and visual comparison through a fixed operator PC worker. Render serves the web/API and computes comparisons. The browser only calls Render; the worker makes outbound HTTPS connections and exposes no inbound PC port.

**The operator PC must be powered on, signed in and online.** This is a free, access-protected preview, not autonomous cloud-only operation. Source policies, availability and page structures still apply. A failed task is not automatically repeated through another source or host.

## Comparison features

- [x] Supplier-free personal comparison and manual input/editing.
- [x] Building type, structure, total height and floor-position boundaries.
- [x] Progressive condition tolerances and separate reference samples.
- [x] Visual price comparison, sample ranks, original links and fixed comparison actions.
- [x] Building identity handling that excludes generic station/height/age advertisement titles from known building names.

## Public-site query integration

- [x] W1: Separate user/worker credentials and fixed validated search/import contracts.
- [x] W2: Bounded claim/lease/result queue, no expired-job redelivery, identical-result upload retry only, cancellation and role isolation.
- [x] W3: Asynchronous URL autofill, service connection status/manual refresh and stale-response protection.
- [x] W4: Deploy the worker broker to the existing free Render service and install a hidden current-user Windows logon task with a separate private token.
- [x] W5: Actual public-site Yahoo URL autofill → SUUMO 60 advertisements → comparison graph, rank and source links; connection loss/recovery preserves the form and existing result.

Active configuration: execution mode worker, search source SUUMO, compatible import sources Yahoo! Real Estate and CHINTAI. Actual live import acceptance in this release used Yahoo; it is not a claim that every compatible URL or source was tested.

## Verification

- Application commit: af7231de6c0c3660ea2afecee466d9ea7594c4e1.
- Render deployment: dep-daj09o8ae00c7385ml40, started 2026-09-13 11:05:21 KST, live 11:05:55 KST.
- GitHub Actions 34732146457 succeeded. Before the final clock correction, full regression was 1,356 passed, one platform skip, 468 subtests passed; the final affected worker/broker/browser suite passed 153 tests.
- Public UI acceptance at 11:06–11:08 KST: actual Yahoo detail autofill with four unknown-field notices; SUUMO three list pages, 60 returned advertisements and 22 enriched details from 24 detail requests; four comparison cards plus the subject in the graph, with rank, condition differences and original URLs.
- Stopping the dedicated worker produced an offline message and disabled source-query actions; the form and comparison remained unchanged. Restarting restored both actions and preserved the same inputs and result. This lifecycle check made no additional source requests; the worker was left running.
- A roughly 1.7-second PC/server clock difference initially caused fresh results to fail future-timestamp validation. Claim responses now carry server UTC; the worker uses elapsed monotonic time and waits up to five seconds before upload. Observation timestamps and OS time are unchanged. Excess skew fails with a fixed error and does not repeat source requests.

## Earlier direct-cloud attempts

- [x] A1–A3: Diagnose failed source stages, implement Yahoo support and publish tested integrations.
- [ ] A4: Direct source access from Render remains unresolved. SUUMO returned HTTP 503, CHINTAI robots returned HTTP 403, and the final direct Yahoo search returned HTTP 403. Their exact remote cause is not established.
- [x] U1–U3: Compatible Yahoo/CHINTAI URL parsing, review of unknown fields, authenticated import and validation.
- [ ] U4: Direct URL retrieval on Render remains unresolved after an access-restriction response. The completed W5 flow above uses the fixed operator PC; it does not establish cloud-only source access.

## Remaining operating limits

No supplier agreement, uninterrupted availability or whole-market valuation is claimed. Incomplete building identity can leave cross-site duplicates; identical price/area alone is not treated as proof of one unit. The preview has one shared access code, a single source worker and transient jobs. Render Free quotas and operator-PC uptime bound its availability. See docs/REMOTE_WORKER.md and docs/DEPLOY_RENDER.md for configuration, lifecycle and stopping instructions.

## Personal access-code change — 2026-09-13

- [x] C1: Support short Unicode codes on the personal page, with NFC normalization and ASCII-safe HTTP header transport. No deployment credential appears in source or tests.
- [x] C2: Keep strong worker/pilot credentials and existing ASCII compatibility; verify wrong/missing credentials, role isolation, normalization and logout. Backend authentication suites: 94 passed; personal browser suites: 22 passed; both JavaScript syntax checks passed.
- [ ] C3: Deploy the change, rotate the private environment value and verify new-code login, rejection of the previous code and worker connectivity on the public service.

