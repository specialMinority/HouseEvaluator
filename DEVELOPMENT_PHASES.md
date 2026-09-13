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
- [ ] A3: Publish the tested integration and deploy it to the existing free service. Yahoo robots and a listing page have returned HTTP 200 from the actual Render runtime; this preflight alone is not full search acceptance.
- [ ] A4: Verify actual cloud search results and comparison graphs, record coverage and limits.

## Listing URL autofill — 2026-09-13

- [x] U1: Parse compatible Yahoo and CHINTAI detail pages using source identity and stated facts; unknown values stay unknown.
- [x] U2: Add authenticated import API, source selection, URL input, review of missing fields, and protection against stale responses and mixed old/new inputs.
- [x] U3: Verify parser, URL/transport restrictions, authentication, deadlines, and offline mobile UI behavior.
- [ ] U4: Verify actual cloud URL import, user review, automatic search and visual comparison after deployment.

Production configuration enables Yahoo search and Yahoo URL import only. Source availability and page structures may change; failed requests do not switch identity or bypass restrictions. No supplier contract, whole-market valuation or uninterrupted availability is claimed.
