# HouseEvaluator v2 development and deployment

Public progress record. Detailed local evidence and user-entered rental examples remain local.

## Existing comparison features

- [x] Supplier-free personal comparison, manual input and editing.
- [x] Building type, structure, total height and floor-position boundaries.
- [x] Progressive comparison tolerances and clearly separated reference samples.
- [x] Visual price comparison, sample ranks, original listing links and fixed comparison actions.
- [x] Access-protected Docker preview deployed on Render Free.

## Automatic search recovery — 2026-09-13

- [x] A1: Identify the actual failed stage. SUUMO returned HTTP 503 for the first listing page from Render; its exact cause remains unclassified. CHINTAI subsequently returned HTTP 403 for robots and was not retried.
- [x] A2: Implement the independent Yahoo! Real Estate source with observed station/theme/page links, strict URL and robots checks, bounded requests, exact station/layout and area scope, and cross-page conflict rejection.
- [x] A3: Publish the tested integration and deploy it to the existing free service. Commit 760f35c passed 1,216 tests and 468 subtests (one platform skip); GitHub Actions 34729022931 succeeded.
- [ ] A4: Cloud acceptance remains incomplete. After the earlier Yahoo preflight returned HTTP 200, the final live application received HTTP 403 on its first listing page. No listings were returned and no retry or identity change was used.

## Listing URL autofill — 2026-09-13

- [x] U1: Parse compatible Yahoo and CHINTAI detail pages using source identity and stated facts; unknown values stay unknown.
- [x] U2: Add authenticated import API, source selection, URL input, review of missing fields, and protection against stale responses and mixed old/new inputs.
- [x] U3: Verify parser, URL/transport restrictions, authentication, deadlines, and offline mobile UI behavior.
- [ ] U4: Cloud URL import received an access restriction and remains incomplete. A separate local browser did complete an actual Yahoo URL import, review, existing SUUMO search (59 returned advertisements), and graph/link flow. Local success does not establish cloud availability.

Production configuration enables Yahoo search and Yahoo URL import only. Source availability and page structures may change; failed requests do not switch identity or bypass restrictions. No supplier contract, whole-market valuation or uninterrupted availability is claimed.

## Latest verification

- Live Render deployment: dep-daivbqu7bikc73a75fc0, 2026-09-13 10:01:58 KST, normal Python server command.
- Actual cloud search and detail import are unavailable in the final acceptance check; basic HTTPS, authentication, static resources and synthetic comparisons passed.
- Preflight and production requests were compared offline and had identical serialized request headers and URLs. The remote cause of the changed response is not established.
- The existing local server was updated without changing the user's open form; it retains SUUMO search and enables Yahoo/CHINTAI URL import.
- Remaining cloud acceptance requires successful actual source access and a full search/import/comparison flow. A source adapter or a preflight response alone does not meet this condition.

## Building identity correction

- [x] Do not count station/height/age advertisement descriptions as known building names, including previously loaded browser data.
- [x] Warn when a source URL cannot establish whether the subject also appears on another site; do not merge units by similar prices or features.
- [x] Final regression: 1,237 passed, one platform skip, 468 subtests passed. Recomparison of the existing local 59 advertisements required no additional source requests and displayed five comparison links with identity warnings.
