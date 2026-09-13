# HouseEvaluator v2

공개 서비스의 조회 작업은 운영자 PC의 인증된 전용 작업자로 분리할 수 있습니다. 웹 화면·비교 계산은 같은 Render 주소를 사용하며 PC 전원·로그인·인터넷 연결이 필요합니다. 설정과 중단 방법은 [조회 작업자 운영 문서](docs/REMOTE_WORKER.md)를 참고하세요. 로컬 직접 실행의 기본값은 기존 `direct` 모드입니다.

일본으로 워킹홀리데이·취업을 준비하는 한국인을 위한 월세 비교 웹앱입니다. 도쿄·오사카·후쿠오카의 입력 매물을 같은 조건의 모집 매물과 비교하여 **월세 + 관리비**의 상대적 위치와 근거를 보여주는 것을 목표로 합니다.

현재 기본 화면은 **공급사 없이 사용하는 개인 비교**입니다. 호환 매물 URL로 조건을 불러오거나 직접 입력한 뒤, 운영 환경에서 선택한 공개 출처에서 비슷한 매물을 검색합니다. 검색 실패나 정보 누락 시 직접 입력·수정하여 비교를 계속합니다. 선택한 광고 표본의 월세+관리비 중앙값과 차이를 보여주며, 시장 전체의 적정 가격이나 현재 공실을 보증하지 않습니다. 사이트 정책·HTML 변경에는 영향을 받으며, 거절된 접근을 우회하지 않습니다.

공급 파일/HTTPS JSON 피드, 시장 검증, 접속 제어, 백업·복구 등 기존 공급형 기능도 보존했습니다. 실제 공급 계약·시장 검증은 아직 완료되지 않았으며, **개인 비교의 선행 조건은 아닙니다**. 개인 비교는 접속 코드가 있는 무료 Render 미리보기로 배포하며 실제 검증 결과는 [배포 기록](docs/DEPLOY_RENDER.md)에 있습니다. 사용법과 제한은 [PERSONAL_COMPARISON.md](docs/PERSONAL_COMPARISON.md)를 참고하세요.

개발 단계별 계획과 완료 기록은 [DEVELOPMENT_PHASES.md](DEVELOPMENT_PHASES.md)에 있습니다. 예전 배포·스크레이핑·등급 평가 설명은 변경 없이 [LEGACY_README.md](docs/LEGACY_README.md)에 보존했습니다.

## 로컬에서 실행하기

현재 v2 작업 내용이 있는 저장소 루트에서 실행합니다. Python 3.12 이상을 사용하며 서버·수집기·SQLite 도구는 표준 라이브러리만 필요합니다. 프런트엔드 빌드나 npm 설치는 필요 없습니다.

```powershell
python -m backend.src.server
```

브라우저에서 [http://127.0.0.1:8000/frontend/v2/](http://127.0.0.1:8000/frontend/v2/)를 엽니다. API 키·공급 파일 없이 개인 비교 화면이 열립니다. 기본 바인딩은 `127.0.0.1:8000`이며 저장소 루트의 `.runtime/v2.sqlite3`가 생성됩니다. 공개 검색 결과는 이 DB에 저장하지 않고 최대 15분 동안 서버 메모리에 보관합니다. 종료는 `Ctrl+C`입니다.

1. **매물 링크로 빠르게 입력**에서 지원하는 상세 URL을 불러오거나 도시·구·일본어 역명과 대상 조건을 직접 입력합니다. 미상 항목을 보완하고 읽어 온 조건을 확인합니다.
2. **비슷한 매물 자동으로 찾기**를 누르고 결과의 원문·조건을 확인합니다.
3. 필요하면 **직접 입력**으로 추가하거나 검색 매물을 수정합니다.
4. 비교할 매물을 선택하고 결과의 조건 차이·완화 이력·누락 정보를 확인합니다.

광고의 맨션·아파트 구분, 건물 지상 총층수, 구조군, 엘리베이터, 거주 층 위치를 먼저 구분합니다. 같은 건물군 안에서 연식·도보·면적 등의 조건을 단계적으로 넓힙니다. 다른 건물군과 정보가 미상인 매물도 **별도 참고가격**으로 보여주지만 주 비교군의 중앙값·대상 대비 차이에 합산하지 않습니다. 예를 들어 8층 맨션과 3층 아파트는 분리합니다. 관리비 미상 시 각 비교군의 금액 기준을 월세만으로 통일하며, 주 비교에서는 대상도 같은 기준을 씁니다. 매물 3개·구분 가능한 건물 3개가 없으면 중앙값 대신 개별 가격과 최저·최고를 보여줍니다. 다른 지역·역·평면은 섞지 않습니다.

비교 버튼은 목록 위·아래와 화면 하단 고정 바에서 사용할 수 있습니다. 결과 상단의 **가격 그래프와 표본 내 순위**로 내 집의 위치를 먼저 확인하고, 바로 아래에서 각 비교 매물의 조건 차이와 원문 링크를 볼 수 있습니다. **결과 보기**로 언제든 돌아갈 수 있습니다.

검색은 공개 페이지에서 확인한 역 링크와 출처별 탐색 경로를 사용합니다. Yahoo! 부동산·CHINTAI는 45초 안에서 최대 3개 목록을 읽고 같은 역·평면·면적 범위의 후보를 선택합니다. Yahoo에서는 실제 제공된 평면별 탐색 링크를 우선합니다. 기존 SUUMO 어댑터는 별도로 최대 24개 상세 조회도 사용합니다. 한 건물의 다수 광고가 후보를 독점하지 않게 나눠 읽습니다. 검색 범위·광고 수·잠정 건물 수·상세 확인 수·종료 사유를 함께 표시하며, 이번 조회에서 비교군을 찾지 못한 것을 주변 매물 전체의 부재로 표현하지 않습니다.

주 비교가 없어도 가장 가까운 건물 조건 단계에서 최대 5개 광고를 골라 내 집과 가격을 나란히 보여줍니다. 매물은 가격과 무관하게 조건으로 선택하고 건물당 1개만 표시합니다. 그래프의 순위는 내 집을 포함한 표시 표본 안의 광고 가격순서이며, 시장 전체 순위나 조건 차이를 보정한 적정가격이 아닙니다. 관리비가 하나라도 미상이면 대상과 그래프 모두 월세만으로 통일합니다. 각 매물의 총층수·연식 등 차이와 실제 원문을 함께 확인할 수 있습니다.

기존 공급형 화면은 `/frontend/v2/licensed.html`에서 사용할 수 있습니다. 개인 기능만 끄려면 `HOUSE_EVALUATOR_PERSONAL=0`을 설정합니다. `HOUSE_EVALUATOR_PILOT=1`인 기존 공급형 제한 공개에서는 개인 API를 끄므로 승인 조건이 우회되지 않습니다.

합성 예시를 사용하려면 별도 터미널에서 명시적으로 데모를 켭니다.

```powershell
# Windows PowerShell
$env:HOUSE_EVALUATOR_DEMO = '1'
python -m backend.src.server
```

```bash
# macOS / Linux
HOUSE_EVALUATOR_DEMO=1 python -m backend.src.server
```

화면에서 합성 시연 모드와 도시를 선택하고 예시 입력을 불러옵니다. 각 도시의 데이터는 개발용으로 만든 24호실·12건물입니다. 합성 시연 표시가 유지되며 실제 시장의 높다/낮다 판정은 생성하지 않습니다. 합성 시각은 데모 요청 시 최대 1시간마다 갱신합니다. 실제 관측의 시각을 임의로 갱신하지 않습니다.

## 기존 공급형 비교의 구현 범위

- 같은 도시·지자체·정확한 역 ID·평면·구조·계약 유형·가구 여부·욕실 분리 조건을 유지합니다.
- 방향 → 면적 → 건축연도 → 역 도보 순서로 조건을 하나씩 제한적으로 완화합니다. 신축 경계와 지하·1층 등 층 구간은 유지합니다.
- 모집 중이고 상태 확인 시각이 24시간 이내인 관측만 사용합니다. 관리비나 핵심 조건 미상을 임의로 채우지 않습니다.
- 자기 매물, 같은 호실의 중복 게시, 충돌하는 모집 상태·가격을 제외합니다. 건물별 가중치로 중앙값·사분위수·유효 표본 수를 함께 계산합니다.
- 조건 완화 과정, 비교 호실·건물 수, 데이터 시각, 출처, 제외 사유와 매물별 차이를 보여줍니다.
- 실제 비교 공급원의 이용권과 유효기간을 검사합니다. 서버에 공급 설정을 연결하면 권리 취소·비활성화·증빙 변경도 기존 자료의 제공 중단에 반영합니다.
- 충분한 표본, 대상 식별, 핵심 조건과 운영자 승인 검증 구간을 모두 충족한 실제 관측에서만 방향 판정이 가능합니다. 기본 검증 구간은 비어 있습니다. `limited`는 참고 분포만, `insufficient`·`stale`은 가격 요약도 보류합니다.

지원 평면은 `1R / 1K / 1DK / 1LDK`, 건물 종류는 독립된 공동주택 호실입니다. 역·지자체 정규 사전과 공급원 사이의 동일 호실 ID 매핑은 실제 공급 연동 단계에서 구축해야 합니다. 기존 포털 스크레이핑과 과거 집계표는 v2 비교 경로에 연결되어 있지 않습니다.

초기비용과 월 추가비용은 별도 계산 API에서 다룹니다. 이 비용 합계는 시장 비교의 월세 + 관리비 기준을 바꾸지 않습니다.

## 실제 데이터 반입

계약상 비교·표시·보관이 허용되고 정규 ID와 상태 확인 시각을 검수한 공급 파일을 준비한 뒤 실행합니다.

```powershell
python scripts/import_v2_snapshot.py .runtime/supplier-snapshot.json --db .runtime/v2.sqlite3
```

`rights` JSON 값을 작성하는 행위가 실제 공급 계약을 대신하지 않습니다. 현재 구현은 공급원별 완결 스냅샷을 원자적으로 교체하고, 과거 스냅샷으로 되돌아가는 반입을 거부합니다. 허용된 로컬 정규 JSON 또는 HTTPS JSON 피드에 주기적으로 요청하는 수집기와 영속 작업 상태·재시도를 구현했습니다. 실제 공급처·피드·증빙은 운영자가 확보해야 합니다. 파일 규격은 [DATA_IMPORT_V2.md](docs/DATA_IMPORT_V2.md), 공급 설정과 철회 반영은 [SUPPLY_OPERATIONS_V2.md](docs/SUPPLY_OPERATIONS_V2.md), 승인과 운영은 [OPERATIONS_V2.md](docs/OPERATIONS_V2.md)를 참고합니다.

실제 공급 설정을 준비한 뒤 한 번 반입합니다. 지속 실행은 `--once` 대신 `--worker`를 사용합니다.

```powershell
python scripts/run_v2_ingestion.py --config .runtime/config/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/ingestion.sqlite3 --once
```

서버에도 같은 `HOUSE_EVALUATOR_SUPPLIERS`와 상태 DB를 연결해야 조기 철회가 반영됩니다. 수집기 설정만 바꾸어 다른 서버의 설정까지 자동 변경되지는 않습니다. 특정 포털 정책을 우회하는 기능은 없습니다.

시간·건물 분리 요청셋, 전문가 검수, 연속 30일 공급 증거가 준비되면 검증 보고서를 생성합니다.

```powershell
python scripts/validate_v2_market.py .runtime/evidence/manifest.json --output .runtime/config/reports/market-001.json
```

보고서는 모집가격 예측 오차·비교 가능률·보류율을 측정하며 실제 계약가격의 적정성을 보증하지 않습니다. 합성 데이터는 시장 검증에 사용할 수 없습니다. 보고서 생성은 운영 승인이 아니며, 승인 시 JSON 파일 SHA256·현재 비교 코드 SHA256·공급원 집합·기한·23개 필수 기준을 확인합니다. [MARKET_VALIDATION_V2.md](docs/MARKET_VALIDATION_V2.md)에 증거 형식과 기준이 있습니다.

## API와 주요 설정

| API | 용도 |
|---|---|
| `GET /api/v2/capabilities` | 지원 도시, 데모 허용 여부, 실제 데이터 가용 상태 |
| `POST /api/v2/evaluate` | `{subject, mode: "market" 또는 "demo"}` 비교 요청 |
| `GET /api/v2/demo-subject?city=tokyo` | 데모가 활성화된 서버의 합성 예시 입력 |
| `POST /api/v2/costs` | 입력한 비용 항목의 별도 합계 계산 |
| `GET /api/v2/health` | 저장소 상태 요약 |
| `GET /api/v2/stations?city=tokyo&mode=market` | 현재 자료의 정규 역 선택 목록 |
| `GET /api/v2/supply-status` | 수집 작업의 성공·실패·최신성 상태 |
| `GET /api/v2/readiness` | 현재 서버 공개 준비 검사: 준비 시 200, 미충족 시 503 |

| 환경 변수 | 기본값 / 용도 |
|---|---|
| `HOST` / `PORT` | 로컬 `127.0.0.1` / `8000` |
| `HOUSE_EVALUATOR_DB` | `.runtime/v2.sqlite3`; SQLite 저장 경로 |
| `HOUSE_EVALUATOR_DEMO` | `0`; `1`일 때 합성 시연 허용 |
| `HOUSE_EVALUATOR_VALIDATION_REGISTRY` | 미설정; 운영자가 승인한 검증 보고서 목록 경로 |
| `HOUSE_EVALUATOR_SUPPLIERS` | 미설정; 서버와 수집기가 함께 사용하는 공급 설정 |
| `HOUSE_EVALUATOR_INGESTION_STATE` | `.runtime/ingestion.sqlite3`; 수집 작업 상태 DB |
| `HOUSE_EVALUATOR_RELEASE_EVIDENCE` | 미설정; 복구·부하·경보·사용성 검토 증거 등록부 |
| `HOUSE_EVALUATOR_ACCESS_TOKEN` | 미설정; 설정하면 모든 API에 Bearer 접속 코드 필요 |
| `HOUSE_EVALUATOR_REQUESTS_PER_MINUTE` | `120`; 직접 연결 IP별 분당 API 요청 한도 |
| `HOUSE_EVALUATOR_PILOT` | `0`; `1`이면 접속 코드·공급 설정 필수, 준비 미충족 평가 차단 |
| `HOUSE_EVALUATOR_LEGACY` | `0`; 회귀용 설정이며 파일럿에서 사용 금지 |
| `HOUSE_EVALUATOR_PUBLIC_SEARCH` | `1`; 공개 검색 및 URL 불러오기 켜기/끄기 |
| `HOUSE_EVALUATOR_SEARCH_SOURCE` | `suumo`; `chintai` 또는 `yahoo_realestate`로 운영 출처 선택. Render 구성은 Yahoo |
| `HOUSE_EVALUATOR_IMPORT_SOURCES` | `chintai,yahoo_realestate`; 허용할 URL 출처 ID. Render는 `yahoo_realestate` |
| `SUUMO_LIVE` | Docker와 CI는 `0`; 구버전 전용. 개인 공개 검색은 위 설정을 사용 |

입력·결과 필드와 요청 크기 제한은 [V2_CONTRACT.md](docs/V2_CONTRACT.md)에 있습니다. 공급원 반입과 검증 승인을 요청 JSON으로 바꾸는 API는 제공하지 않습니다.

접속 코드는 공백 없는 ASCII 32~256자이며 화면 입력값은 브라우저 메모리에만 둡니다. URL에 코드를 넣지 않습니다. 파일럿에서는 데모·레거시를 모두 꺼야 합니다. HTTP는 16개 작업 슬롯·연결 시작 후 절대 10초 제한·JSON 본문 64 KiB 제한을 사용합니다.

`scripts/check_v2_release.py`는 지정한 로컬 DB·설정으로 `demo=False`인 실행 구성을 사전 검사합니다. 실행 중인 원격 서버나 현재 서버의 데모 환경 변수는 조사하지 않습니다. 실제 실행 중인 서버는 해당 서버의 `/api/v2/readiness`를 확인합니다.

## 백업·복구·보존 관리

SQLite 온라인 백업, 해시·기한 검사 후 새 경로 복구, 만료·철회·보존 기간 초과 자료의 정리 도구가 있습니다. 정리는 기본 계획 출력이며 `--apply`로 실행합니다. 운영 DB에 복구 파일을 덮어쓰지 않습니다.

```powershell
python scripts/maintain_v2_store.py backup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups
```

기본 보존 기간 7일은 개발 가정이며 실제 계약에 맞춰야 합니다. backup·cleanup·restore에 `--suppliers .runtime/config/suppliers.json`을 전달하면 조기 철회·계약 종료도 검사합니다. 잘못된 설정으로 삭제를 진행하지 않으며 `--revoke-source`로 명시한 대상도 함께 적용합니다. `--suppliers`를 생략하면 스냅숏 원문 권리와 명시적 철회만 검사합니다. 자동 삭제 일정·원격 백업·외부 경보 발송은 등록하지 않았습니다. 운영 저장소는 영속 로컬 SQLite만 지원하며 PostgreSQL/PostGIS·다중 인스턴스는 구현 범위 밖입니다. [STORAGE_LIFECYCLE_V2.md](docs/STORAGE_LIFECYCLE_V2.md)에 상세 절차가 있습니다.

## 테스트

테스트를 실행할 때만 pytest를 설치합니다. 기존 구현의 오프라인 회귀와 v2 데이터·비교·API 테스트를 함께 실행합니다. HTTP 테스트는 로컬 서버를 사용하고 포털 응답은 고정 테스트 데이터로 대체합니다.

```powershell
python -m pip install pytest==9.0.2
$env:SUUMO_LIVE = '0'
$env:HOUSE_EVALUATOR_DEMO = '0'
$env:HOUSE_EVALUATOR_LEGACY = '0'
python -m pytest -q
node --check frontend/v2/app.js
```

Node.js는 JavaScript 문법 검사에만 필요합니다. [.github/workflows/tests.yml](.github/workflows/tests.yml)은 Python 3.12·3.14의 회귀 테스트와 Node.js 24의 문법 검사를 구성합니다. 테스트 통과는 합성·고정 입력에 대한 구현 검증이며 실제 시장 정확도를 의미하지 않습니다.

2026-09-13 전체 검증은 **356 passed, 1 skipped, 61 subtests**입니다. 프로그램 동작 검증 결과이며 실제 시장 정확도·운영 배포를 증명하지 않습니다. 상세 범위는 [PHASE67_VALIDATION.md](docs/PHASE67_VALIDATION.md)에 기록합니다.

## Docker 로컬 실행

```bash
docker build -t houseevaluator:v2-local .
docker volume create houseevaluator-v2-data
docker run --rm --name houseevaluator-v2 -p 127.0.0.1:8000:8080 -v houseevaluator-v2-data:/data houseevaluator:v2-local
```

컨테이너 안의 포트는 `8080`이며 브라우저 주소는 로컬 실행과 동일합니다. 합성 시연이 필요하면 `docker run`에 `-e HOUSE_EVALUATOR_DEMO=1`을 추가합니다. 이미지 기본값은 데모·레거시·포털 조회 모두 비활성화입니다.

이미지는 v2 서버·프런트엔드와 반입·수집·시장 검증·백업·복구·부하·준비 검사 CLI를 포함하며 UID/GID `10001`로 실행합니다. `HOUSE_EVALUATOR_DB=/data/v2.sqlite3`, `/data` 및 `/backups` 볼륨을 사용하므로 컨테이너를 교체해도 지정한 볼륨의 데이터가 유지됩니다. 직접 호스트 폴더를 바인드 마운트하면 UID `10001`의 쓰기 권한이 필요합니다. 운영 데이터의 보관·백업·권리 종료 시 파기 정책은 별도로 정해야 합니다.

Phase 7.3에서 Docker Desktop Linux 엔진을 기동하고 이미지 빌드·비루트 실행·강제 종료 복구·컨테이너 재생성·데이터 보존·공개 차단·복구·로컬 알림 훈련을 검증했습니다. 실제 자료와 외부 이용자 대상 공개는 아직 진행하지 않았습니다. 로컬 검증을 다시 실행하려면 새 보고서 폴더를 지정합니다. 이 명령은 빈 공급 설정과 합성 자료만 사용하며 자신이 만든 임시 컨테이너·볼륨·이미지를 정리합니다.

```powershell
python scripts/rehearse_v2_pilot.py --output-dir .runtime/reports/pilot-rehearsal-001
```

무료 우선 설정은 [FREE_FIRST_DEPLOYMENT.md](docs/FREE_FIRST_DEPLOYMENT.md), 외부 TLS 구성의 준비 조건은 [PILOT_DEPLOYMENT_V2.md](docs/PILOT_DEPLOYMENT_V2.md)를 따릅니다. 장애·회복 기록 도구는 [ALERTS_V2.md](docs/ALERTS_V2.md)에 있으며 외부 메시지를 보내지 않습니다.

## 개발 문서

| 문서 | 내용 |
|---|---|
| [DEVELOPMENT_PHASES.md](DEVELOPMENT_PHASES.md) | 단계별 세부 계획, 완료 표시, 남은 작업 |
| [PRODUCT_PLAN_V2.md](docs/PRODUCT_PLAN_V2.md) | 저장소 검토와 재설계 기획 |
| [V2_CONTRACT.md](docs/V2_CONTRACT.md) | 입력·데이터·비교·API 계약 |
| [COMPARISON_POLICY_V2.md](docs/COMPARISON_POLICY_V2.md) | 조건 완화, 가중 분포, 판정 기준과 한계 |
| [DATA_IMPORT_V2.md](docs/DATA_IMPORT_V2.md) | 스냅샷 반입·저장·권리 검사 |
| [OPERATIONS_V2.md](docs/OPERATIONS_V2.md) | 실행·승인·상태 확인과 운영 절차 |
| [SUPPLY_OPERATIONS_V2.md](docs/SUPPLY_OPERATIONS_V2.md) | 공급 설정·지속 수집·권리 철회 |
| [MARKET_VALIDATION_V2.md](docs/MARKET_VALIDATION_V2.md) | 시장 검증 증거·지표·보고서 |
| [STORAGE_LIFECYCLE_V2.md](docs/STORAGE_LIFECYCLE_V2.md) | 저장소·백업·복구·보존 정리 |
| [FREE_FIRST_DEPLOYMENT.md](docs/FREE_FIRST_DEPLOYMENT.md) | 무료 우선 파일럿 준비와 배포 조건 |
| [PHASE67_VALIDATION.md](docs/PHASE67_VALIDATION.md) | Phase 6~7 검증 결과와 미검증 범위 |
| [PILOT_DEPLOYMENT_V2.md](docs/PILOT_DEPLOYMENT_V2.md) | Phase 7.3 로컬 배포와 향후 TLS 연결 |
| [PILOT_USABILITY_V2.md](docs/PILOT_USABILITY_V2.md) | 한국인 대상 사용성 검수 양식 |
| [LEGACY_README.md](docs/LEGACY_README.md) | 변경 전 개발·배포 설명 보존본 |
