# Tokyo WH House Evaluator — Context & Roadmap (Live Comparable Benchmark)

## 0) TL;DR (현 상태)
- 이 프로젝트의 목표는 **일본(주로 도쿄) 원룸/1K/1DK/1LDK 임대 매물**을 입력하면, 위치/컨디션/비용 3개 축으로 점수를 매기고 “비싸다/적정/싸다” 판단 근거를 제공하는 것입니다.
- 기존에는 수동 수집된 벤치마크(CSV/JSON)만으로 비교했는데, 이를 **매물 사이트에서 동일 조건 매물 3개 이상을 실시간으로 찾고(필요 시 조건 완화), (월세+관리비) 중간값(median)을 벤치마크로 써서 평가에 반영**하는 방향으로 확장했습니다.
- 현재는 **핫픽스 수준에서 정상 작동(사용자 확인)**하는 상태이며, SUUMO/CHINTAI 파서와 URL 리졸버의 구조적 취약점(재발 가능)을 줄이기 위한 **부분 리라이트 + URL 리졸버 구조 개선**은 “다음 단계”로 남겨두었습니다.

---

## 1) 프로젝트 목적 (왜 만들었나)
### 문제
- 일본 원룸/1K/1DK 계약은 “초기비용(敷金/礼金/仲介/保証 등)”이 크고, 매물 비교를 수동으로 하면 **유지보수/확장/정확성**이 떨어집니다.
- 특히 **벤치마크 데이터가 월세만 포함(관리비 없음)**인 경우, 입력 매물(월세+관리비)과 비교 시 항상 “비싸다”로 편향되는 문제가 있습니다.

### 목표
- 사용자가 URL 또는 직접 입력으로 조건을 넣으면:
  1) 동일 조건의 매물을 매물 포털(예: CHINTAI/SUUMO 등)에서 검색/추출
  2) (월세+관리비) 기준으로 **중간값(median)**을 산출
  3) 결과를 평가(비용 점수/근거/리스크)로 반영
- 비교 실패 시에는 기존 벤치마크 인덱스(CSV/JSON)로 안전하게 폴백합니다.

---

## 2) 아키텍처/데이터 흐름
### 전체 흐름
- Frontend(바닐라 JS SPA) → `POST /api/evaluate` → Backend(Python)에서:
  - 입력 검증(S1 스펙)
  - 파생값 계산(월 고정비, 연식, 초기비용 배수 등)
  - 벤치마크 매칭(라이브 또는 인덱스)
  - 점수/등급/리포트 생성(S2/C1 스펙)

### 라이브 벤치마크 파이프라인(현재 구현)
1. 조건 입력(`prefecture`, `municipality`, `layout_type`, `area_sqm`, `station_walk_min`, `built_year`, `orientation`, `bath_sep`, `structure` 등)
2. Provider 순서 결정(`LIVE_PROVIDERS`, 기본: `chintai,suumo`)
3. Provider별 검색 URL 생성
4. 검색 결과 페이지 fetch
5. HTML 파싱 → `SuumoListing`(정규화된 룸 단위 레코드) 리스트 생성
6. 사용자 조건 매칭(단계별 relaxation 적용)
7. 매칭된 목록에서 **(월세+관리비) median** 산출 → 벤치마크로 사용

### 폴백
- 라이브에서 confidence가 `none`이면, `benchmark_index.json` 기반 벤치마크로 폴백합니다.
- 데이터셋이 **월세만 포함**하므로, 폴백 벤치마크일 때는 보수적으로 관리비 추정을 더해 편향을 줄입니다(라이브 벤치마크에서는 실제 관리비를 쓰므로 추정 스킵).

---

## 3) 프로젝트 구조(요약)
- `backend/src/evaluate.py`: 평가 메인(입력 검증, 파생값, 라이브/폴백 벤치마크, 점수/리포트)
- `backend/src/live_benchmark.py`: 라이브 비교 Provider 디스패처(순차 시도 + 실패 누적)
- `backend/src/chintai_scraper.py`: CHINTAI 라이브 스크래퍼(목록 파싱 + 필요 시 상세 페이지 보강)
- `backend/src/suumo_scraper.py`: SUUMO 라이브 스크래퍼(목록 파싱 + relaxation)
- `backend/src/homes_scraper.py`: HOME’S 스크래퍼(현재 WAF/JS 챌린지로 실패 가능성이 높아 기본 제외)
- `backend/data/benchmark_index.json`: 폴백용 벤치마크 인덱스(로컬에 존재하면 재사용)
- `agents/agent_D_benchmark_data/out/benchmark_rent_raw.json`: 원천 벤치마크 데이터(월세 중심)
- `frontend/src/ui/benchmarkSection.js`: 결과 화면의 “벤치마크/실시간 비교” 카드 렌더링 + 디버그 출력

---

## 4) 지금까지 작업 로그(Phase별 요약)
### Phase A — UX/동작 오류 정리
- URL 파싱/평가 결과가 안 나오던 문제(프론트 JS 에러 포함)를 해결해 “평가하기”가 동작하도록 복구.

### Phase B — 라이브 비교(실시간 comparables) 도입
- `SUUMO_LIVE=1`일 때 실시간 비교를 수행하도록 `evaluate.py`에 통합.
- `derived.live_benchmark`에 provider order, search_url, attempts(디버그) 등을 노출하도록 구성.
- (관리비 편향) 폴백 벤치마크(월세-only)에는 보수적 관리비 추정을 추가, **라이브 벤치마크에서는 추정 스킵**하도록 분기.

### Phase C — CHINTAI Provider 추가 + relaxation 구현
- CHINTAI 목록 페이지에서 룸 단위로 파싱(월세/관리비/면적/레이아웃/역/도보/연식/구조 등).
- 부족한 필드(방위/욕실분리/구조 등)는 detail_url로 1회 보강(fetch) 가능하게 구성.
- relaxation(step)에서 숫자 버킷(면적/도보/연식)을 단계적으로 넓히고,
  - step2부터 구조 필터 완화, step3부터 욕실분리 완화 등으로 표본 확보 가능성을 높임.

### Phase D — “즉시복구(핫픽스)” (지금 정상동작에 직접 기여한 변경)
#### D1) CHINTAI URL 리졸브 안정화
- `benchmark_index`의 CHINTAI source_url이 `/rent/`(시세) 페이지여도, 라이브 비교는 룸 표본이 필요하므로 `/list/`를 우선 사용하도록 수정.
- `sources`에 `/list/`가 없고 `/rent/`만 있으면 area code를 추론해 `/list/` base URL을 합성.
- 입력 `municipality`가 “東京都江戸川区南小岩5” 같은 **주소 전체 문자열**이어도 인덱스 키(`江戸川区`)를 찾도록 정규화/퍼지 매칭/코드 추론 로직 추가.

#### D2) SUUMO 파싱 핫픽스 (레이아웃 empty → 전부 탈락 문제 완화)
- SUUMO 검색 결과 모드에 따라 “레이아웃(間取り)이 건물 헤더에만 1회 표기되고 방 row에는 빠지는” 케이스가 있어,
  - block 전체에서 레이아웃 토큰이 **유일하게 1개**면 row에 레이아웃이 없을 때 fallback으로 채우도록 수정.
- 면적 표기 `m^2`, `m^{2}`까지 파싱 허용 패턴 확장.
- attempt 로그에 `coverage`(layout/area/walk/age/structure/bath/station 파싱 개수)를 추가하고, 프론트에서 `cov:`로 표시하도록 개선.

---

## 5) 현재 제약/한계 (핫픽스로 해결하지 못한 “구조적” 위험)
- **SUUMO/HOME’S는 WAF/JS 렌더링/차단이 발생 가능**합니다.
  - urllib 기반(헤드리스 브라우저 없음)으로는 JS 렌더링된 결과를 가져올 수 없습니다.
  - WAF/봇 차단 우회는 하지 않습니다(정상적인 fallback/대체 경로만 제공).
- **정규식 기반 텍스트 파싱은 HTML 구조 변화에 취약**합니다.
  - 핫픽스는 “지금 당장 돌아가게” 만드는 목적이고, 재발 방지는 다음 단계에서 구조적으로 해결합니다.

---

## 6) 다음 단계(미래 작업) — 부분 리라이트 계획(재발 방지)
> 지금은 보류. “나중에” 진행할 작업 계획만 구체화해 둠.

### 6.1 SUUMO 파서 부분 리라이트(권장)
**목표**
- block 텍스트 split/정규식 의존도를 줄이고, 룸 row(테이블/카드)의 컬럼을 기준으로 안정 파싱.

**설계 방향**
- `HTMLParser`로 DOM-ish state machine을 만들고:
  - 건물(cassette) 컨텍스트(구조/연식/역 등) 파싱
  - 룸 row 단위로 (층/월세/관리비/間取り/専有面積) 파싱
  - 건물 헤더 값은 row에 누락된 경우에만 “컨텍스트 fallback”으로 사용(오라벨링 방지)

**품질 계측(필수)**
- attempt마다:
  - fetched_n, parsed_n
  - `layout_non_empty_ratio`, `rent_parse_success_ratio`, `area_parse_success_ratio`
  - 샘플 1~2개(레이아웃/면적/월세/관리비) 출력(민감정보 없음)

**회귀 테스트(권장)**
- 대표 HTML 스냅샷을 `fixtures/`로 저장(표시 모드별 2~3개).
- “계약 테스트”로 파서 품질을 수치로 고정:
  - `parsed_n > 0`
  - `layout_non_empty_ratio > 0.9` 등

### 6.2 CHINTAI 파서 하드닝
- 현재 파서는 비교적 안정적이나, 페이지/마크업 변형에 대비해:
  - 룸 row 추출 로직(예: `tbody[data-detailurl]`)의 fallback 경로 추가
  - detail fetch 최소화(필요할 때만) + 캐시 강화

### 6.3 매칭 로직 안정화(하드필터 vs 소프트스코어)
- 지금은 “필드 누락 = 즉시 탈락”이 많아, 파서가 조금만 흔들려도 0건이 됩니다.
- 개선 방향:
  - 절대조건(레이아웃 등)과 가변조건(방위/구조/욕실분리)을 분리
  - 개별 누락은 penalty로 흡수하되, **누락 비율이 높으면 파서 회귀로 판단해 provider 실패 처리**(진단 가능하게)

### 6.4 표본(3개) 안정성 개선
- 네트워크 비용 대비 3개 랜덤 표본은 분산이 큼.
- 개선 방향:
  - 매칭된 것 중 최대 20~30개까지 확보 후 median(또는 trimmed median)
  - IQR/분산 기반 confidence 산정

---

## 7) 다음 단계(미래 작업) — URL 리졸버 구조 개선 계획
> 지금은 보류. “나중에” 진행할 작업 계획만 구체화해 둠.

### 7.1 현재 문제(왜 자주 깨졌나)
- `benchmark_index.json`의 `sources[].source_url`은 “목록(/list/)”과 “시세(/rent/)” 등 목적이 다른 URL이 섞여 있고,
  - 단순히 첫 URL을 선택하면 잘못된 타입을 집어오는 위험이 큼.
- 또 입력 `municipality`가 주소 전체로 들어오면 인덱스 key와 불일치(정확 match 실패)합니다.

### 7.2 목표 상태
- “벤치마크 인덱스”는 가격/출처 용도로 유지하되,
  - **라이브 비교 URL은 인덱스 임의 URL에 의존하지 않는** 별도 리졸버로 생성.
- Provider별로 필요한 “코드/그리드”를 명시적으로 관리:
  - SUUMO: `sc` 코드, md 그리드, rent/area/age grid
  - CHINTAI: `area/<JIS>/list/` + query/path 필터

### 7.3 구현 계획(안)
1) Resolver 인터페이스 도입
- 예: `resolve(provider, prefecture, municipality, layout_type, ...) -> ResolvedSearch`  
  (리턴에 `list_url`, `rent_url`, `provider_meta`, `errors` 등 포함)

2) Municipality 매핑 소스 분리
- `backend/data/municipality_codes.json` 같은 파일에:
  - `tokyo: { 江戸川区: { jis: 13123, suumo_sc: 13123, chintai_area: 13123 } }` 형태로 저장
- 갱신은 “수동 스크립트”로만(자동 크롤링은 최소화)

3) URL 타입 스키마화
- (인덱스를 계속 쓴다면) 최소한 `list_url` vs `rent_url`를 분리해 구조적으로 실수를 방지.

4) 실패 분류를 표준화
- `url_build_failed`, `provider_waf_blocked`, `fetch_failed`, `parse_regression_suspected`, `no_matches_after_filter` 등

---

## 8) 운영/디버깅 메모
### 실행
```powershell
python -m backend.src.server
```

### 라이브 비교 켜기/끄기
```powershell
$env:SUUMO_LIVE="1"         # on
$env:SUUMO_LIVE="0"         # off
$env:LIVE_PROVIDERS="chintai,suumo"
python -m backend.src.server
```

### 무엇을 보면 “라이브가 실제로 사용”됐는지
- 응답의:
  - `derived.live_benchmark.used == true`
  - `derived.benchmark_matched_level`이 `*_live` 또는 `*_relaxed`
  - `derived.benchmark_n_sources >= 3`
  - `derived.live_benchmark.search_url` 확인

---

## 9) TODO (나중에 진행)
- [ ] SUUMO DOM 기반 룸-row 파서 리라이트 + fixtures 계약 테스트
- [ ] URL 리졸버 분리(인덱스 URL 의존 제거) + municipality code 캐시/갱신 스크립트
- [ ] 매칭 로직(하드/소프트) 분리 + 파서 회귀 자동 감지
- [ ] 표본 수 확대 + IQR 기반 confidence 개선
- [ ] 문서 업데이트(`backend/README.md`의 LIVE_PROVIDERS 기본값 등은 현재 코드와 불일치 가능)

