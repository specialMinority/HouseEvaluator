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
- [x] C3: Deployed and rotated the private user code. Actual Chrome login succeeded; new-code API returned 200, previous-code and unauthenticated API returned 401. The worker remained online and URL/search actions were available. The private local code file was updated; source requests during authentication verification: zero.

Application commit: 0d8aa6ad278c6c94e8f55de9cbe2927b33022bd4. Render deployment dep-daj6ao5g1s2s739ijitg became live at 2026-09-13 17:57:35 KST; public authentication verification completed at 17:58 KST. The worker credential was unchanged.

## Direct Korean typing — 2026-09-13

- [x] C4: Replace password-type access fields with text inputs that support Korean IME, disable spelling/capitalization corrections, suppress composition-confirmation Enter submission and defer button submission until composition completes. Preserve NFC normalization, existing code and credential separation.
- [x] C5: Deployed and verified the text input, completed-composition login, paste-compatible login and online query connection against the public service. Partial composition sent no authenticated request; browser errors and source jobs were zero. Tests use synthetic events and native Chromium composition commands, not the user's physical Windows keyboard.

Affected browser regression: 37 passed; both JavaScript syntax checks passed. Live application commit dc65e1f1724a01296231419a8b9954f194162926, Render dep-daj70a1594qs73b29u90, live 2026-09-13 18:43:48 KST; public composition/login verification completed at 18:44 KST. Deployment credentials were unchanged.

## PC 조회 작업자 보안 — 2026-09-13

- [x] S1: 원격 명령 실행 경로, 요청 검증, 키 권한과 작업자 실행 방식 점검. 수신 포트 공개 없는 발신 구조를 확인.
- [x] S2: 비관리자 Docker 작업자, 읽기 전용 실행 파일, 개인 폴더 미공유, CPU·메모리·프로세스·로그 제한.
- [x] S3: 작업자 네트워크 차단, 별도 Unix 소켓 통신 통로의 고정 목적지·공인 IP·시간·바이트·동시 연결 제한. 실제 격리 검사 통과.
- [x] S4: 재시작 후 유지되는 시간별·일별 작업 예산, 별도 통신량 예산, 개인 키 파일 ACL 축소, 잘못된 인증 시도 한도 강화.
- [ ] S5: 전체 회귀·공개 배포·실제 자동검색/URL 조회·시작/중단 검증 마무리.

상세: docs/PC_WORKER_SECURITY.md. 컨테이너·동일 사용자 악성코드·공급 사이트 제한 등 잔여 위험은 문서에 분리한다. PC 전체의 무감염 판정이나 공격 불가 보증이 아니다.
