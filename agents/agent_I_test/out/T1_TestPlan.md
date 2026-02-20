# T1 Test Plan (Agent I: QA/테스트)

- Version: 0.1.0
- Last updated: 2026-02-17
- Scope: Tokyo WH House Evaluator (MVP)

## 0) References (MUST)
- `R0_Requirements.md`
- `V0_Vocabulary.yml` (canonical keys/enums, risk_flag IDs)
- `agents/agent_C_scoring/out/S2_ScoringSpec.json` (risk flags / tradeoff rules / scoring params)
- `agents/agent_E_copy/out/C1_ReportTemplates.json` (summary/evidence template rules)

## 1) Goals / Non-goals
### Goals
- 점수 계산만이 아니라 아래 3가지를 함께 검증한다.
  - `trade-off` 문장(요약) **규칙 선택**
  - `risk_flags` **발생/표기**
  - `benchmark_confidence` **폴백 메시지**
- 골든 입력은 `prefecture` 3현(`tokyo|saitama|chiba`)을 **10/10/10**으로 커버한다.
- 모든 입력 JSON은 **V0에 정의된 key만 사용**한다.

### Non-goals (본 T1에서 다루지 않음)
- 포털/공공 데이터 수집 파이프라인 품질(샘플 수/정규화 정확도) 자체의 검증
- 법률/계약 자문 수준의 검증(문구는 “가능성/권장” 톤 유지만 확인)

## 2) Under Test (contracts)
### 2.1 Input keys (MVP required 12)
입력(골든 JSON)에서 20개는 아래 **12개 키만** 사용한다.
- `hub_station`
- `prefecture`
- `nearest_station_name`
- `station_walk_min`
- `layout_type`
- `area_sqm`
- `building_built_year`
- `orientation`
- `bathroom_toilet_separate`
- `rent_yen`
- `mgmt_fee_yen`
- `initial_cost_total_yen`

### 2.2 Advanced optional keys (breakdown/contract)
나머지 10개 입력은 비용 breakdown/계약 리스크 키를 포함한다(예: `reikin_yen`, `brokerage_fee_yen`, `key_change_yen`, `cleaning_fee_yen`, `other_initial_fees_yen`, `contract_term_months`, `early_termination_penalty_yen` 등).

## 3) Test Levels
### L1. Unit tests (추천)
- Derived 계산기: `monthly_fixed_cost_yen`, `initial_multiple`, `building_age_years`
- Scoring 계산기: `location_score`, `condition_score`, `cost_score`, `overall_score` + grade band
- JSONLogic Rule engine:
  - S2 `risk_flag_rules[]` 평가
  - S2 `tradeoff_rules[]` 평가
  - C1 `rules[]` 템플릿 선택(최대 `priority` rule 1개)
- 템플릿 렌더러: `{placeholder}` 치환 + 미치환 토큰/미등록 토큰 검출

### L2. Integration tests (가능한 범위)
- 입력(JSON) → (벤치마크 매칭) → derived → scoring → risk flags → report template 선택/렌더까지 end-to-end.
- 벤치마크 데이터가 외부/미포함인 환경에서는, 벤치마크 결과(`benchmark_monthly_fixed_cost_yen`, `benchmark_confidence`)를 **주입/모킹**하는 형태로 CI에서 재현성 확보.

### L3. Regression tests (Golden Inputs)
- 본 문서의 `G0_GoldenInputs` 30개를 고정 세트로 사용.
- 목적: 규칙/가중치/문구 변경 시, risk flags/템플릿 선택/파생값이 의도치 않게 바뀌지 않는지 탐지.

## 4) Test Cases

### 4.1 Derived values (정확도/경계)
**TC-DER-01** `monthly_fixed_cost_yen`  
- Given: `rent_yen`, `mgmt_fee_yen`  
- Expect: `monthly_fixed_cost_yen = rent_yen + mgmt_fee_yen` (정수 덧셈, 오버플로 없음)

**TC-DER-02** `initial_multiple`  
- Given: `initial_cost_total_yen`, `monthly_fixed_cost_yen`  
- Expect: `initial_multiple = initial_cost_total_yen / monthly_fixed_cost_yen`  
- Edge:
  - `monthly_fixed_cost_yen > 0` 정상 케이스(소수 유지)
  - `monthly_fixed_cost_yen == 0`(방어): crash 없이 처리(예: `null`/0/에러 메시지) — 제품 정책에 맞춰 확정 필요

**TC-DER-03** `building_age_years`  
- Given: `building_built_year`, 평가 연도(런타임 `current_year`)  
- Expect: `building_age_years = max(0, current_year - building_built_year)`  
- Edge:
  - 미래 연도(`building_built_year > current_year`)면 0 클램프

### 4.2 Risk flags (S2 rule 기반: 최소 1회 이상 발생)
검증 포인트:
- `risk_flags` 배열에 **V0 risk_flag ID만** 들어간다.
- 각 플래그는 최소 1회 이상 트리거(골든 세트 기준 “가능한 범위” 충족).
- 경계값 테스트 포함(==, >=, < 등).

| risk_flag_id | Rule (S2) 요약 | 경계/대표 Golden |
|---|---|---|
| `HIGH_INITIAL_MULTIPLE` | `initial_multiple >= 6.0` | `listing_007.json` (IM=6.0), `listing_001.json` (IM=3.0) |
| `FAR_FROM_STATION` | `station_walk_min >= 15` | `listing_002.json` (15), `listing_011.json` (14), `listing_015.json` (16) |
| `VERY_SMALL_AREA` | `area_sqm < 15` | `listing_003.json` (14.5), `listing_010.json` (15.0) |
| `OLD_BUILDING` | `building_age_years >= 30` | `listing_004.json` (built 1996), `listing_011.json` (built 1997) |
| `NORTH_FACING` | `orientation == "N"` | `listing_005.json`, `listing_020.json`, `listing_028.json` |
| `BATH_NOT_SEPARATE` | `bathroom_toilet_separate == false` | `listing_006.json`, `listing_016.json`, `listing_028.json` |
| `HIGH_OTHER_FEES` | `other_initial_fees_yen >= 100000` (present) | `listing_021.json` (100000), `listing_022.json` (80000) |
| `LONG_CONTRACT_TERM` | `contract_term_months >= 24` (present) | `listing_022.json` (24), `listing_027.json` (12) |
| `HIGH_EARLY_TERMINATION_PENALTY` | `early_termination_penalty_yen >= 100000` (present) | `listing_022.json` (100000), `listing_023.json` (absent), `listing_027.json` (150000) |

### 4.3 Trade-off rule selection (S2 + C1)
**절대 규칙:** “점수만”이 아니라, **trade-off 문장 선택**까지 검증한다.

#### 4.3.1 S2 `tradeoff_rules[]` (tag/message_key)
검증:
- 조건이 겹치면 `priority`가 높은 rule이 선택된다.
- 선택 결과 `tradeoff_tag`, `message_key`가 의도와 일치한다.

대표 케이스(컨텍스트 주입 Unit Test 권장):
- **TC-TRD-S2-01** condition 높고 cost 낮음 → `condition_high_cost_low`
  - Given: `condition_score >= 75`, `cost_score <= 55`
- **TC-TRD-S2-02** location 높고 condition 낮음 → `location_high_condition_low`
  - Given: `location_score >= 75`, `condition_score <= 55`
- **TC-TRD-S2-03** location 낮고 cost 높음 → `location_low_cost_high`
  - Given: `location_score <= 55`, `cost_score >= 75`
- **TC-TRD-S2-99** 그 외 → `balanced`

#### 4.3.2 C1 `rules[]` (요약 템플릿 선택)
검증:
- `when` 조건을 만족하는 rule 중 **최고 `priority` 1개**가 선택된다.
- 선택된 rule의 `summary_ko`에 trade-off가 최소 1회 포함되고, `{placeholder}`가 모두 치환된다.

필수 시나리오(컨텍스트 주입 Unit Test 권장):
- **TC-TPL-01** `benchmark_confidence == "none"` → `R01_BENCHMARK_NONE`
- **TC-TPL-02** `benchmark_confidence == "low"` → `R02_BENCHMARK_LOW`
- **TC-TPL-03** `condition_score >= 70` & `cost_score <= 40` → `R03_CONDITION_HIGH_COST_LOW` (단, benchmark rule 미해당)
- **TC-TPL-04** `location_score >= 70` & `condition_score <= 40` → `R04_LOCATION_HIGH_CONDITION_LOW`
- **TC-TPL-05** `cost_score >= 70` & `location_score <= 40` → `R05_COST_HIGH_LOCATION_LOW`
- **TC-TPL-06** `overall_score >= 75` & 각 subscore>=55 & `benchmark_confidence in ["high","mid"]` → `R06_BALANCED_STRONG`
- **TC-TPL-07** `initial_multiple >= 6.0` & `cost_score <= 55` → `R07_HIGH_INITIAL_MULTIPLE`
- **TC-TPL-08** `station_walk_min >= 16` → `R08_FAR_FROM_STATION`
- **TC-TPL-99** 어떤 rule도 매칭되지 않으면 → `R99_FALLBACK_GENERAL`

### 4.4 Benchmark confidence fallback messaging
검증:
- `benchmark_confidence`가 `none/low`일 때, 요약/근거 bullet에 “신뢰도”가 명시된다(C1 R01/R02).
- S2 cost feature(`rent_delta_ratio`)는 `benchmark_confidence in ["none","low"]`이면 중립점수 처리(스코어 급락 방지).
- Integration(가능 시): 가상의 역명으로 station-level 매칭 실패를 유도해 `benchmark_confidence="none"` 경로를 확인.

## 5) Golden Inputs Set (G0) — Coverage Summary
- 경로: `agents/agent_I_test/out/G0_GoldenInputs/`
- 총 30개: `listing_001.json` ~ `listing_030.json`
- 3현 커버: `tokyo` 10 / `saitama` 10 / `chiba` 10
- 20개: MVP required 12 keys only (`listing_001`~`listing_020`)
- 10개: breakdown/contract 포함 (`listing_021`~`listing_030`)

### 5.1 Risk flag coverage mapping (intended)
- `HIGH_INITIAL_MULTIPLE`: 007, 018, 021, 025
- `FAR_FROM_STATION`: 002, 008, 015, 024, 028
- `VERY_SMALL_AREA`: 003, 012, 016, 028
- `OLD_BUILDING`: 004, 008, 017, 028
- `NORTH_FACING`: 005, 020, 028
- `BATH_NOT_SEPARATE`: 006, 008, 016, 023, 028
- `HIGH_OTHER_FEES`: 021, 025
- `LONG_CONTRACT_TERM`: 022, 026
- `HIGH_EARLY_TERMINATION_PENALTY`: 022, 027

### 5.2 Benchmark fallback mapping (best-effort)
- `benchmark_confidence` none 경로 유도(역명 비현실): `listing_014.json` (`nearest_station_name="架空駅テスト"`)

## 6) Exit Criteria (Definition of Done)
- (정적) 모든 Golden JSON이 V0 input key만 사용, required 타입/enum 범위 내.
- (동적) 최소 1회 이상: 각 risk_flag_id 트리거.
- (동적) trade-off/템플릿: 위 TC-TPL 필수 시나리오가 모두 통과.
- (동적) benchmark_confidence none/low 시, 폴백 메시지가 출력되고 cost_score가 중립 처리됨.
