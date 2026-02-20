# Tokyo WH House Evaluator — R0 Requirements (v0.1.0)
- Last updated: 2026-02-17
- Status: Frozen (R0). Changes require proposal + orchestrator approval.
- Target locale: Korean (primary), Japanese terms included in report.

## 1. Purpose
도쿄(생활권: 사이타마–도쿄–치바)로 워킹홀리데이를 오는 한국인이 구한 원룸/1K 매물의
- (1) 집 컨디션(향/연식/면적/기본 설비 등)
- (2) 입지(사용자가 선택한 기준 거점 접근성 + 역 도보)
- (3) 비용(월 고정비 + 초기비용)
을 시장 벤치마크에 정규화해, **상대적으로 좋은 조건인지**를 트레이드오프 중심 리포트로 제공한다.

## 2. Product Principles
1) “싸다/비싸다” 단정 대신, **트레이드오프(좋은 점/아쉬운 점)**를 근거와 함께 설명한다.  
2) 워홀 관점에서 중요한 **현금흐름(초기비용 부담) + 계약 리스크**를 우선 경고한다.  
3) 입력 부담을 낮추기 위해 필수 입력은 **10~12개로 제한**한다.  
4) 외국인 계약에서 발생할 수 있는 불리함은 **상수 가중치(조정치)**로 반영하되, 과도한 비용은 여전히 경고한다.  
5) 결과는 “평가”로 끝나지 않고 **협상/대안 탐색(일본어 키워드 포함)**으로 이어지게 한다.

## 3. In Scope (MVP)
### 3.1 Coverage
- Region: Saitama / Tokyo / Chiba / Kanagawa (Yokohama) (prefecture level required)
- Housing type: 1R, 1K 중심 (MVP는 1R/1K만)
- Currency: JPY only

### 3.2 User Inputs (contract-only costs)
**IMPORTANT:** 입력 비용은 “임대 계약서에 찍히는 비용”만 포함한다. (이사비, 가구/가전 구매비 등 제외)

#### Required fields (12)
1) hub_station (user selects)
2) prefecture (saitama|tokyo|chiba|kanagawa)
3) nearest_station_name (string)
4) station_walk_min (int)
5) layout_type (1R|1K)
6) area_sqm (number)
7) building_built_year (int)  (or age bucket is allowed in advanced mode; MVP required is built_year)
8) orientation (N/NE/E/SE/S/SW/W/NW/UNKNOWN)
9) bathroom_toilet_separate (bool)
10) rent_yen (int)
11) mgmt_fee_yen (int)
12) initial_cost_total_yen (int) — total only (breakdown optional)

#### Optional (advanced toggle)
- municipality (string)
- line_name (string)
- initial cost breakdown (all yen):
  - shikikin_yen, reikin_yen, brokerage_fee_yen, guarantor_fee_yen,
    fire_insurance_yen, key_change_yen, cleaning_fee_yen, other_initial_fees_yen
- contract risk:
  - contract_term_months, renewal_fee_months, early_termination_penalty_yen
- notes_free_text (string) — not used for scoring in MVP, for user memo only

### 3.3 Benchmarking
- Baseline benchmarks come from portal/public datasets (implementation details in D1 spec).
- Matching priority (fallback):
  1) (prefecture + nearest_station_name) level if available
  2) (prefecture) level
- Benchmark dimensions (MVP):
  - layout_type (1R/1K)
  - area bucket (e.g., <15, 15–19, 20–24, 25–29, 30+ sqm)
  - building age bucket (e.g., 0–5, 6–10, 11–20, 21+ years)
  - station_walk bucket (e.g., <=5, 6–10, 11–15, 16+ min)

### 3.4 Foreigner premium (constant adjustment)
MVP에서는 외국인 프리미엄을 “상수 조정치”로만 반영한다.
- Purpose: 외국인에게 흔히 발생하는 추가 비용을 감안하여 **오판(무조건 비쌈)**을 줄이되,
  과도한 비용(눈탱이 가능성)은 계속 경고한다.

Default constants (tunable in S2):
- foreign_im_shift_months: +1.0
  - Meaning: 초기비용 배수(IM) 등급 경계값을 “1개월”만큼 완화한 보조 기준을 함께 제공
- foreign_reikin_allowance_months: +1.0
  - Meaning: 礼金(레이킹) 월수 판단 시 허용 범위를 1개월 상향한 보조 기준 제공

Report must show:
- “General benchmark result” and “Foreigner-adjusted view” side-by-side (or clearly labeled).

## 4. Scoring & Indices (MVP)
### 4.1 Derived values
- monthly_fixed_cost_yen = rent_yen + mgmt_fee_yen
- initial_multiple = initial_cost_total_yen / monthly_fixed_cost_yen
- rent_delta_ratio = (monthly_fixed_cost_yen - benchmark_monthly_fixed_cost_yen) / benchmark_monthly_fixed_cost_yen
  - if benchmark unavailable => fallback to prefecture-level or mark as “benchmark_low_confidence”

### 4.2 Subscores (0–100 each)
- location_score: benchmark-based area access score (if available) + station_walk component
  - if access benchmark unavailable: use prefecture baseline + station_walk
- condition_score: area_sqm, building_age, orientation, bathroom separation 중심
- cost_score: rent_delta + initial_multiple 중심 (initial_multiple is key for WH)

### 4.3 Overall score
- overall_score = weighted_sum(location, condition, cost) (weights in S2 spec)
- output grades: A/B/C/D for overall and each subscore

### 4.4 Trade-off narrative (must)
Report must generate at least 1 trade-off sentence based on:
- condition_score high & cost_score low => “컨디션 좋지만 비용 부담”
- location_score high & condition_score low => “입지 좋지만 컨디션 아쉬움”
- location_score low & cost_score high => “저렴하지만 입지 불리”
(Exact thresholds and wording in C1 + S2)

### 4.5 Risk flags (rule-based)
MVP flags are rule-based (no ML). Examples:
- HIGH_INITIAL_MULTIPLE
- FAR_FROM_STATION
- VERY_SMALL_AREA
- OLD_BUILDING
- HIGH_OTHER_FEES (only if breakdown provided)
- LONG_CONTRACT_TERM (advanced input)
- HIGH_EARLY_TERMINATION_PENALTY (advanced input)

## 5. Report Output (MVP)
### 5.1 Output structure (must)
1) One-paragraph summary (Korean), including trade-off
2) Score cards (overall + 3 subscores) with grades
3) Evidence bullets (3–5): computed values + benchmark comparisons
4) Risk flags + short explanation
5) Negotiation suggestions (JP phrases included)
6) Alternatives: search keyword queries in Japanese (portal-friendly)
7) What-if simulation:
   - If breakdown absent: “initial_cost_total_yen reduction slider” scenario
   - If breakdown present: toggle/adjust (reikin/brokerage/key/cleaning) scenarios

### 5.2 Tone
- Avoid legal guarantees. Use “가능성/경향/권장” language.
- Be explicit about confidence when benchmarks are missing or fallback used.

## 6. Non-goals (MVP)
- 자동 매물 추천/크롤링/실시간 가격 조회
- 계약서/개인정보 업로드 및 저장
- 법률/세무 자문
- 일본어 통역/대리 협상

## 7. Free WebApp Constraints (MVP)
- No login required
- Mobile-first
- Fast: simple forms, no heavy dependencies required
- Privacy: do not store personal identifiers by default

## 8. Acceptance Criteria (MVP “done”)
- Required 12 inputs only로 리포트 생성 가능
- 벤치마크가 없을 때도(폴백/저신뢰 표시) 결과가 깨지지 않음
- 외국인 조정 관점이 결과에 명확히 표시됨
- 협상 문구(일본어 포함) + 대안 검색 쿼리가 최소 3개 이상 출력됨
- GoldenInputs 회귀 테스트에서 점수/문장 규칙이 일관되게 동작함

## 9. Change Management
- Any change to keys/enums must update:
  - V0_Vocabulary.yml
  - K0_OutputContracts schemas
  - GoldenInputs regression expectations (if any)
