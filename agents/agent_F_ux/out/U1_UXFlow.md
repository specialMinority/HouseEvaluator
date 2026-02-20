# U1 UX Flow (Agent F: UX/IA)
모바일 우선 · 무료 · 로그인 없음. 필수 입력은 **12개**만 받고, 나머지는 **고급(advanced) 토글**로 확장한다.

> 참고: `agents/agent_B_inputschema/out/S1_InputSchema.json` 는 현재 `{}`(비어있음). 입력 키/검증은 `R0_Requirements.md`, `V0_Vocabulary.yml` 기준으로 설계.

---

## 0) 전체 플로우
1. **화면1 입력**: 필수 12개 입력 → (선택) 고급 토글 → `리포트 생성`
2. **화면2 리포트**: 요약 → 점수 → 근거 → 플래그 → 협상 → 대안 → What-if
3. (선택) **공유 링크**: 개인식별 정보 없이(특히 메모 제외) 링크 생성/복사

---

## 1) 화면1: 입력 폼 (그룹/순서/검증/예시/도움말)
### IA/레이아웃(모바일)
- 1열 스크롤 폼 + 섹션 헤더(스티키) + 섹션 단위 접기/펼치기(기본 펼침)
- 상단: 서비스명, “초기비용(IM) 중심” 한 줄 설명, “개인정보 저장 안 함” 라벨
- 하단 고정 CTA: `리포트 생성` (필수값 유효할 때만 활성)

### 필수 입력(12개) — **고정(항상 노출)**
아래 12개만 “필수”로 강제한다(10~12 규칙 준수).

#### A. 위치/교통
1) `hub_station` (필수, enum)
- UI: 라디오/칩 선택( `tokyo_station | shinjuku | shibuya | ikebukuro | ueno | shinagawa | other` )
- 도움말: “주요 허브 기준 ‘접근성’ 비교에만 사용(정확한 노선 탐색 아님).”
- 조건부: `hub_station == 'other'` 일 때만 `hub_station_other_name` 텍스트 입력 노출
  - 예시: “예: kichijoji / 吉祥寺”

2) `prefecture` (필수, enum)
- UI: 세그먼트( `saitama | tokyo | chiba` )
- 검증: 미선택 불가

3) `nearest_station_name` (필수, string)
- UI: 텍스트(IME 지원) + 예시 플레이스홀더
- 예시: “高田馬場 / 타카다노바바”
- 검증: 공백만 입력 불가(1자 이상)
- 도움말: “벤치마크 매칭 우선순위: (도도부현+역명) → (도도부현).”

4) `station_walk_min` (필수, int, 0~60)
- UI: 숫자 스텝퍼(분)
- 예시: 7
- 검증: 0~60, 정수
- 도움말: “부동산 표기 도보시간은 체감과 다를 수 있어요(신호/경사).”

#### B. 집 기본
5) `layout_type` (필수, enum)
- UI: 2옵션 토글( `1R` / `1K` )
- 도움말: “벤치마크 버킷(1R/1K)과 직접 매칭.”

6) `area_sqm` (필수, number, 5~80)
- UI: 숫자 입력(소수 1자리 허용) + 단위 `m2`
- 예시: 21.5
- 검증: 5~80
- 도움말: “면적이 작으면 체감 불편/수납 리스크 플래그가 뜰 수 있어요.”

7) `building_built_year` (필수, int, 1950~2035)
- UI: 연도 입력(4자리) + “대략” 토글(고급에서 ‘연식 버킷’으로 대체 입력은 허용 가능하나 MVP 필수는 연도)
- 예시: 2017
- 검증: 1950~2035, 정수
- 도움말: “연식은 벤치마크 ‘건물나이 버킷’에 들어가요.”

8) `orientation` (필수, enum)
- UI: 드롭다운( `N, NE, E, SE, S, SW, W, NW, UNKNOWN` )
- 기본값: `UNKNOWN` 허용(그래도 필수 선택으로 처리)
- 도움말: “북향은 채광/건조 체감 이슈가 있을 수 있어요(플래그: NORTH_FACING).”

9) `bathroom_toilet_separate` (필수, bool)
- UI: 스위치(분리/일체)
- 도움말: “선호에 따라 점수 영향(플래그: BATH_NOT_SEPARATE).”

#### C. 비용(계약서 기준, 생활비 제외)
10) `rent_yen` (필수, int, 0~500000)
- UI: 숫자 입력 + 천단위 콤마 + 단위 JPY
- 예시: 98000
- 검증: 0~500000, 정수

11) `mgmt_fee_yen` (필수, int, 0~100000)
- UI: 숫자 입력 + 단위 JPY
- 예시: 8000 (없으면 0)
- 검증: 0~100000, 정수
- 도움말: “월 고정비 = 임대료 + 관리비( `monthly_fixed_cost_yen` ).”

12) `initial_cost_total_yen` (필수, int, 0~2000000)
- UI: 숫자 입력 + 단위 JPY
- 예시: 360000
- 검증: 0~2000000, 정수
- 도움말: “계약 시 1회 납부 총액(시키킨/레이킨/중개/보증/보험/열쇠/청소 등 합). 생활용품/가구는 제외.”

### 고급(advanced) 토글 — **기본 접힘(선택)**
토글 라벨: `고급 설정(선택) · 더 정확한 근거/플래그/What-if`

#### D. 추가 위치 정보(선택)
- `municipality` (string) 예: “豊島区 / 토시마구”
- `line_name` (string) 예: “山手線 / 야마노테선”

#### E. 초기비용 상세(선택, 모두 JPY)
- 목적: “HIGH_OTHER_FEES” 같은 플래그 근거 강화 + What-if(항목별 협상) 가능
- 필드: `shikikin_yen, reikin_yen, brokerage_fee_yen, guarantor_fee_yen, fire_insurance_yen, key_change_yen, cleaning_fee_yen, other_initial_fees_yen`
- UX 가드레일:
  - “상세 합계” 자동합산 표시(읽기전용)
  - (권장) `상세 합계` 가 `initial_cost_total_yen` 를 초과하면 경고(저장 가능하되 “총액/상세 불일치” 배지 표시)

#### F. 계약 리스크(선택)
- `contract_term_months` (0~60) 예: 24
- `renewal_fee_months` (0~2, 소수 허용) 예: 1.0
- `early_termination_penalty_yen` (0~2000000) 예: 100000

#### G. 메모(선택, 공유 제외)
- `notes_free_text` (string)
- 안내문: “점수에 반영되지 않으며, **공유 링크에 포함되지 않음**(PII 입력 금지 권장).”

---

## 2) 화면2: 리포트 (R0 구조 그대로)
### 공통 상단(모바일)
- 상단바: `← 입력 수정` / `공유` / “벤치마크 신뢰도” 배지(`benchmark_confidence`)
- 신뢰도 배지 탭 시: “매칭 수준(역/도도부현) + 표본 부족 가능성 + 해석 주의” 바텀시트

### 보기 전환(필수: 일반 vs 외국인 가산)
- 세그먼트: `일반 벤치마크` / `외국인 가산 보기(+1.0개월 기준)`
- 도움말: “MVP 상수( `foreign_im_shift_months`, `foreign_reikin_allowance_months` )로 ‘보수적’ 관점 보조. 법적/확정 판단 아님.”

### 1) 한 문단 요약(요약 + 트레이드오프 포함)
- 출력: `summary_ko`
- 구성: “등급/점수(전체) + 핵심 트레이드오프 1문장 + 주의(신뢰도 낮을 때) 1구절”

### 2) 점수 카드(전체 + 3개 서브)
- 출력: `overall_grade/score`, `location_grade/score`, `condition_grade/score`, `cost_grade/score`
- UI: 4개 카드(세로 스택) + 간단 바(0~100)
- 카드 탭 시: 해당 점수에 기여한 대표 근거(근거 섹션으로 스크롤)

### 3) 근거(Evidence bullets 3~5)
- 출력: `evidence_bullets_ko`
- 반드시 포함(최소 3개):
  - `monthly_fixed_cost_yen` (임대료+관리비)
  - `initial_multiple` (= `initial_cost_total_yen` / `monthly_fixed_cost_yen`)
  - 벤치마크 비교(가능 시): `rent_delta_ratio` + `benchmark_monthly_fixed_cost_yen`

### 4) 플래그(리스크 + 설명)
- 출력: `risk_flags` (+ 규칙 설명)
- UI: severity 배지(`low/mid/high`) + 1~2줄 설명
- “없음” 상태: “뚜렷한 계약 리스크 신호는 적어요(단, 벤치마크 신뢰도에 따라 해석 주의).”

### 5) 협상 제안(한국어 설명 + 일본어 문구)
- 출력: `negotiation_suggestions.ko`, `negotiation_suggestions.ja`
- UI: 항목 카드(제안 요지) + `일본어 문구 복사` 버튼
- 가드레일: “요청/권장” 톤, 확정/강요 표현 금지

### 6) 대안(검색 쿼리, 일본어)
- 출력: `alternative_search_queries_ja`
- UI: 쿼리 리스트 + `복사` + (선택) 포털용 키워드 배지( `1R/1K`, 도도부현, 역, 徒歩 등)

### 7) What-if 시뮬레이션(인터랙션)
- 출력: `what_if_results`
- UI 원칙: “가정(입력)”과 “결과(점수/근거)”를 한 화면에서 즉시 비교

---

## 3) What-if 인터랙션 설계(슬라이더/토글)
### 상태/표시(공통)
- 상단에 “현재값(기준)” 고정 표시: `initial_cost_total_yen`, `initial_multiple`, `cost_score`, `overall_score`
- 변경 시 “가정 적용 중” 배지 + `원상복귀` 버튼
- 결과는 “일반/외국인 가산” 보기 탭에 따라 별도로 계산/표시(탭 전환 시 동일 가정 적용)

### A) 상세 breakdown **없을 때**
- 슬라이더 1개: `초기비용 합계 할인` (0%~30% 기본, 직접입력으로 0~50% 허용)
- 즉시 업데이트:
  - `initial_cost_total_yen` (가정값)
  - `initial_multiple`
  - `cost_score`, `overall_score` (변화량 Δ 표시)
- 도움말: “총액을 한 번에 낮추는 가정(예: ‘초기비용 3만엔 감액’). 실제 협상은 항목별이 더 현실적일 수 있어요.”

### B) 상세 breakdown **있을 때**
- “항목별 가정” 토글(기본 ON)
- 항목 4개에 대해 빠른 프리셋 + 미세조정:
  - `reikin_yen`: 프리셋 `0원 가정(레이킨 0)` / `-50%`
  - `brokerage_fee_yen`: 프리셋 `-50%` / `-100%(무료)`
  - `key_change_yen`: 프리셋 `-50%` / `0원 가정`
  - `cleaning_fee_yen`: 프리셋 `-50%` / `0원 가정`
- 각 항목: (선택) 퍼센트 슬라이더(0~100) 또는 금액 직접입력(모바일 접근성 고려해 숫자 입력 우선)
- “가정 후 총 초기비용” 자동 재계산 → `initial_cost_total_yen` 가정값으로 반영

---

## 4) 벤치마크가 low/none일 때 메시지/표현
### `benchmark_confidence = low`
- 배지: “신뢰도 낮음”
- 배너 문구(짧게): “유사 매물 표본이 적어 **오차가 클 수 있어요**. 도도부현/버킷 기준으로 보수적으로 비교했습니다.”
- 근거 섹션에서: 벤치마크 비교 항목에 “참고용” 라벨 추가

### `benchmark_confidence = none`
- 배지: “벤치마크 없음”
- 배너 문구(짧게): “해당 조건과 매칭되는 벤치마크가 없어 **비교 근거가 제한**됩니다. 계산값(IM 등) 중심으로 판단하세요.”
- 점수 카드:
  - 점수는 표시하되, 카드 하단에 “비교 데이터 부족” 마이크로카피
  - Evidence에서 `rent_delta_ratio` 는 “비교 불가”로 표시(0 처리/숨김 금지)
- CTA 제안(옵션): “역명을 단순화/도도부현만으로 다시 보기” 안내

---

## 5) 공유 링크 정책(개인식별 정보 없이)
### 원칙
- 로그인 없음, 서버 저장 기본값 없음(Privacy by default)
- 링크에는 **개인식별 정보(PII) 및 자유서술 메모**를 포함하지 않는다.

### 포함/제외
- 포함(기본): 필수 12개 입력 + (선택) 고급 입력 중 구조화된 숫자/선택 값(초기비용 breakdown, 계약 리스크, `municipality`, `line_name`)
- 제외(항상): `notes_free_text` (자유서술), 그리고 앱이 향후 수집할 수 있는 어떤 계정/연락처 정보(없어야 함)

### 구현 가이드(UX 관점)
- “공유” 탭 시 확인 모달:
  - “링크에 포함되는 항목” 체크리스트 미리보기
  - “역명/지역은 개인식별 정보는 아니지만 위치 정보입니다” 고지
- 링크 포맷: URL **fragment**(예: `#d=...`) 기반(서버 로그/레퍼러 노출 최소화)
- “복사 완료” 토스트 + “링크 열기(미리보기)” 버튼

---

## 6) 품질 체크리스트(자기검증)
- [x] 필수 입력이 **12개**로 제한됨(조건부 `hub_station_other_name` 은 `other` 선택 시에만 노출/요구)
- [x] 고급(advanced) 토글이 “정확도/What-if 강화” 목적과 포함 항목이 명확함
- [x] 화면2 리포트 섹션 순서가 R0 **5.1 Output structure**(요약→점수→근거→플래그→협상→대안→What-if)를 그대로 따름
