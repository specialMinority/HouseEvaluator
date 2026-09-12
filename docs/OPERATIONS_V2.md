# HouseEvaluator v2 운영 절차

현재 구현은 무료 우선의 로컬 단일 인스턴스 파일럿 준비용이다. 실제 공급 계약, 도시별 시장 검증, 운영 배포는 아직 완료되지 않았다. 단계별 진행의 정본은 [DEVELOPMENT_PHASES.md](../DEVELOPMENT_PHASES.md), 배포 안내는 [FREE_FIRST_DEPLOYMENT.md](FREE_FIRST_DEPLOYMENT.md)다.

## 1. 실행과 데이터 모드

현재 v2 코드가 있는 저장소 루트에서 Python 3.12 이상으로 실행한다. 런타임·수집기·SQLite 도구는 표준 라이브러리만 필요하다.

```powershell
python -m backend.src.server
```

기본 주소는 `http://127.0.0.1:8000/frontend/v2/`다. 실제 자료가 없으면 판단을 보류하며 기존 집계·추정 자료를 대신 사용하지 않는다.

```powershell
$env:HOUSE_EVALUATOR_DEMO = '1'
python -m backend.src.server
```

데모는 화면에서도 합성 시연을 선택해야 한다. 도시별 24호실·12건물은 가상 자료다. 처음 데모를 이용하고 이후 1시간 이상 경과한 요청에서 합성 시각을 갱신한다. 실제 자료는 새 공급 관측 없이 시각을 갱신하지 않는다. 두 모드는 분리해서 읽고 합성 판정은 시장 승인과 무관하게 보류한다.

역 목록은 현재 허용된 자료에서 만든다. 정규 역 ID로 정확히 비교하며 이름의 부분 문자열을 사용하지 않는다. 실제 자료가 없으면 실제 역 목록도 비어 있다. 공급처 사이의 호실·건물·역 매핑은 실제 도입 시 검수한다.

## 2. 서버 설정과 접속

| 환경 변수 | 기본값 | 의미 |
|---|---|---|
| `HOST` / `PORT` | `127.0.0.1` / `8000` | 수신 주소와 포트 |
| `HOUSE_EVALUATOR_DB` | `.runtime/v2.sqlite3` | 영속 로컬 SQLite 파일 |
| `HOUSE_EVALUATOR_SUPPLIERS` | 없음 | 실제 공급 설정·증빙·철회 상태 |
| `HOUSE_EVALUATOR_INGESTION_STATE` | `.runtime/ingestion.sqlite3` | 수집기의 상태 DB |
| `HOUSE_EVALUATOR_VALIDATION_REGISTRY` | 없음 | 운영자가 관리하는 시장 승인 JSON |
| `HOUSE_EVALUATOR_RELEASE_EVIDENCE` | 없음 | 운영 증거 JSON 등록부 |
| `HOUSE_EVALUATOR_ACCESS_TOKEN` | 없음 | API 접속 코드 |
| `HOUSE_EVALUATOR_REQUESTS_PER_MINUTE` | `120` | 직접 연결 IP별 분당 API 요청 한도, 1~10000 설정 |
| `HOUSE_EVALUATOR_PILOT` | 꺼짐 | `1`이면 준비 기준 미충족 비교 요청을 503으로 차단 |
| `HOUSE_EVALUATOR_DEMO` / `HOUSE_EVALUATOR_LEGACY` | 꺼짐 / 꺼짐 | 명시적 개발·회귀 기능, 파일럿에서 둘 다 금지 |

접속 코드는 공백 없는 ASCII 32~256자다. 설정 시 모든 `/api/` 요청에서 `Authorization: Bearer <접속 코드>`를 검사한다. 정적 화면은 코드 입력을 위해 열려 있다. 브라우저는 코드를 메모리에만 두고 URL·localStorage에 저장하지 않는다. 잘못된 인증은 401, 요청 초과는 429와 `Retry-After: 60`이다. 인증 성공과 실패 요청의 한도는 별도로 계산하므로 틀린 코드로 정상 사용자의 한도를 소진할 수 없다. 직접 연결 IP만 사용하며 클라이언트의 `X-Forwarded-For`를 신뢰하지 않는다. **같은 프록시를 거치는 정상 사용자들은 기본 120회/분 API 한도를 합산해 공유한다. 사용자별 120회가 아니며 검색 결과 폴링도 포함된다.** 코드가 없는 로컬 모드는 기존처럼 직접 연결 IP별 한도를 사용한다.

HTTP는 16개 작업 슬롯, 소켓 유휴 읽기 제한과 연결 시작 후 **절대 10초 제한**을 사용한다. 헤더·본문을 조금씩 보내도 무한정 점유하지 못한다. 슬롯 초과는 503, JSON 본문 64 KiB 초과는 413, 잘못된 Content-Type은 415다. 중복 키·NaN·Infinity·잘못된 JSON·비정상 경로를 거절한다. 정적 파일은 지정 목록만 제공하며 요청 URL·쿼리·접속 코드를 로그에 남기지 않는다.

기본 로컬 개발 모드는 인증·공개 준비 차단을 자동으로 켜지 않는다. 파일럿 모드는 접속 코드와 공급 설정이 없거나 데모·레거시가 켜졌으면 시작을 거절한다. 인터넷 공개, TLS, 운영 책임과 네트워크 접근 범위는 별도 배포 설정이 필요하다. [FREE_FIRST_DEPLOYMENT.md](FREE_FIRST_DEPLOYMENT.md)를 따른다.

레거시 URL 자동 조회 API는 항상 410이다. v2는 `SUUMO_LIVE`와 관계없이 포털 조회를 사용하지 않는다. 기존 회귀에서는 `SUUMO_LIVE=0`을 유지한다.

## 3. 공급 반입과 지속 수집

실제 비교·표시·보관 권리와 필드 의미를 확인한 완전 스냅숏만 사용한다. 형식은 [DATA_IMPORT_V2.md](DATA_IMPORT_V2.md), 공급 설정은 [SUPPLY_OPERATIONS_V2.md](SUPPLY_OPERATIONS_V2.md)를 따른다.

```powershell
python scripts/import_v2_snapshot.py .runtime/permitted-snapshot.json --db .runtime/v2.sqlite3
```

빈 스냅숏은 해당 공급원의 모집 자료가 전부 종료되었음을 뜻한다. 실제로 확인한 경우에만 `--allow-empty`를 사용한다. 현재 자료와 해시 영수증을 원자적으로 기록한다. 실패 시 이전 정상 자료를 보존하지만 권리·최신성 기준을 완화하지 않는다. 정정은 더 새로운 `as_of`와 새 ID의 올바른 스냅숏으로 한다.

정규 JSON 파일 또는 명시적으로 허용된 HTTPS JSON 피드의 지속 반입 구현이 있다. 실제 피드·인증키·증빙은 제공되지 않았다. `config/suppliers.example.json`에 따라 운영자가 공급 설정을 작성한다.

```powershell
python scripts/run_v2_ingestion.py --config .runtime/config/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/ingestion.sqlite3 --once
python scripts/run_v2_ingestion.py --state-db .runtime/ingestion.sqlite3 --status
```

계속 실행하려면 `--once` 대신 `--worker`를 쓴다. 주기·lease·재시도·재개 상태를 별도 DB에 유지한다. HTTP 304나 동일 스냅숏 재수신은 원천 시각을 바꾸지 않는다. 저널에는 고정 오류 코드를 기록하며 URL·토큰·계약 원문을 넣지 않는다. 이메일·메신저 경보는 전송하지 않고 상태 API와 로컬 저널로 확인한다.

**서버와 수집기에 같은 공급 설정 및 상태 DB를 연결해야 한다.** 서버의 `HOUSE_EVALUATOR_SUPPLIERS`가 연결되어 있으면 매 조회마다 공급 활성화·취소·기한·증빙 해시를 검사하여 기존 스냅숏도 사용 중단한다. 미연결 개발 모드는 스냅숏 자체 권리만 검사하므로 수집기 설정만 변경해 별도 서버의 조기 철회까지 반영된다고 가정하면 안 된다. 파일럿에서 공급 설정은 필수다.

```powershell
$env:HOUSE_EVALUATOR_SUPPLIERS = '.runtime/config/suppliers.json'
$env:HOUSE_EVALUATOR_INGESTION_STATE = '.runtime/ingestion.sqlite3'
python -m backend.src.server
```

`GET /api/v2/health`는 필터 적용 후 자료 개수·상태, `/api/v2/supply-status`는 수집 상태를 반환한다. 두 API도 접속 코드 설정 시 인증이 필요하다. 복구나 장애 시 만료 자료를 현재 자료처럼 노출하지 않는다.

## 4. 시장 검증과 승인

실제 자료가 있어도 승인 전에는 높은/낮은 판정을 보류한다. [MARKET_VALIDATION_V2.md](MARKET_VALIDATION_V2.md)에 따라 시간·건물 분리 보류 요청, 전문가 검수, 연속 30일 공급 증거를 준비하고 고정 기준으로 검증한다.

```powershell
python scripts/validate_v2_market.py .runtime/evidence/manifest.json --output .runtime/config/reports/market-001.json
```

JSON·Markdown 보고서를 새 파일에 쓴다. 종료 코드 0은 승인 검토 가능한 구간 존재, 3은 증거 미충족 보고서 생성, 2는 입력 오류다. `--smoke`는 합성 전용 프로그램 점검이고 성공해도 시장 승인 자격은 없다. 검증 프로그램은 승인 등록부를 변경하지 않는다.

초기 [validation_registry.example.json](validation_registry.example.json)은 비어 있다. 담당자가 실제 보고서를 검토한 뒤 별도로 등록한다. 아래는 미승인 형식 예시이며 해시·ID·일자는 실제 값으로 바꿔야 한다.

```json
{
  "schema_version": "2.0",
  "reports": [{
    "policy_version": "direct-v2.0",
    "approved": false,
    "approved_by": "실제 검토 담당자",
    "source_ids": ["실제로 검증한 공급원 ID"],
    "segments": ["tokyo/1K"],
    "validated_at": "2026-09-13T00:00:00Z",
    "expires_at": "2026-09-20T00:00:00Z",
    "report_path": "reports/market-001.json",
    "report_sha256": "실제 JSON 파일 바이트의 SHA256"
  }]
}
```

서버는 승인자·승인 여부·정책 버전·승인 유효기간과 **현재 이용 가능한 전체 공급원 ID 집합**의 일치를 검사한다. 등록부 아래 JSON 보고서의 SHA256, `schema_version=market-validation-1.0`, `report_kind=market`, 현재 `comparison.py`의 SHA256도 확인한다. 보고서 생성은 승인보다 늦지 않아야 하고 현재 기준 30일 이내여야 한다.

보고서의 `eligibility.passed`, 승인 요청 구간의 `eligible_for_approval`와 **23개 필수 검증 기준이 모두 boolean true**여야 한다. 누락·미측정·false·숫자 1은 통과가 아니다. 등록부와 보고서는 각각 2,000,000바이트 이하여야 한다. CLI의 큰 전체 보고서를 임의로 잘라 승인하는 방식은 사용하지 않는다. 보관·보고 구조 변경은 별도 구현과 검토가 필요하다.

```powershell
$env:HOUSE_EVALUATOR_VALIDATION_REGISTRY = '.runtime/config/registry.json'
python -m backend.src.server
```

`capabilities.validation_status=configured`는 유효한 승인 구간이 최소 하나 있다는 뜻이다. 개별 `validation_status=approved_segment`와 비교 품질 기준이 실제 판정을 결정한다. 표본이 없는 역권·구조·계약 유형까지 정확도가 입증된 것은 아니다. 자료·보고서의 진위, 블라인드 검수, 공개 범위의 적절성은 운영자가 확인한다. 일반 HTTP 요청이나 공급 데이터는 승인을 바꾸지 못한다.

## 5. 공개 준비와 운영 증거

`GET /api/v2/readiness`는 **실행 중인 해당 서버**의 설정·자료를 검사한다. 전부 통과하면 200, 미충족이면 503이다. `ready`, `blockers`, `eligible_segments`, `segment_availability`, `context_fingerprint`를 확인한다.

각 승인 구간에 24시간 이내 활성·핵심 조건 완전 자료가 최소 20개 고유 호실·10개 건물 있어야 한다. 이는 최소 재고 확인이며 요청별 역·면적·연식·유효 표본 수 비교는 엔진이 다시 검사한다. 다른 도시의 최신 자료는 승인 구간의 부족을 대신하지 못한다.

공급 계약·시장 승인·정상 수집기·접속 보호·데모/레거시 비활성 외에 복구 훈련·평가 API 부하·경보 점검·사용성 검토가 필요하다. `config/release_evidence.example.json`에서 시작해 증거별 `{path, sha256}`로 보고서를 고정한다. 종류·`passed`·`scope=market_operations`·코드/공급 설정 fingerprint를 검사한다. 운영 증거는 7일 이내, 사용성 검토는 30일 이내여야 하며 합성 점검은 출시 증거가 아니다.

파일럿의 구독 비용 예산은 월 0엔이고 실제 예산 확인자·운영자 공개 승인이 별도로 필요하다. 예제에는 승인자가 없다. fingerprint는 `backend/v2` Python 파일 전체, 서버, v2 화면 자산, 공급 설정·공급원 집합을 포함하여 변경 후 예전 증거를 재사용하지 못하게 한다.

로컬 실행 구성을 **사전 검사**할 때는 다음 CLI를 사용한다.

```powershell
python scripts/check_v2_release.py --db .runtime/v2.sqlite3 --suppliers .runtime/config/suppliers.json --state-db .runtime/ingestion.sqlite3 --registry .runtime/config/registry.json --evidence .runtime/config/release.json --output .runtime/config/reports/readiness-001.json
```

이 명령은 지정한 파일로 `Runtime(demo_enabled=False)`를 새로 구성하고 지정 환경 변수의 접속 코드를 확인한다. **실행 중인 서버·원격 URL을 검사하지 않으며 서버의 데모·레거시 환경 변수도 읽지 않는다.** 실제 서버는 별도로 `/api/v2/readiness`를 확인한다. 준비됨은 종료 코드 0, 미충족은 2, 실행 오류는 1이며 배포나 승인을 수행하지 않는다.

복구 훈련은 운영 DB를 덮어쓰지 않고 임시 경로에서 논리적 해시·개수를 비교한다.

```powershell
python scripts/drill_v2_restore.py --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --suppliers .runtime/config/suppliers.json --state-db .runtime/ingestion.sqlite3 --output .runtime/config/reports/restore-001.json
```

허용된 실제 공급 설정·자료가 없거나 합성 자료가 섞였으면 합성 점검 범위다. 보고서를 자동 등록하지 않으며 현재 성공 시연을 실운영 복구 완료로 표현하지 않는다.

평가 부하는 루프백 서버에서 명시한 요청셋으로 점검한다. 아직 공개하지 않은 로컬 서버에서 먼저 실행하고 증거 검토 후 파일럿 모드를 켠다.

```powershell
python scripts/load_test_v2.py --url http://127.0.0.1:8000 --subjects .runtime/evidence/load-subjects.json --mode market --requests 60 --concurrency 4 --output .runtime/config/reports/load-001.json
```

출시 부하 증거는 `workload=evaluate_market`, 최소 60요청·동시성4·고유 대상10호실/5건물·95% 이상 참고가격 결과를 요구한다. 현재 측정 도구의 통과 기준은 전 요청 200과 P95 5초 이내이며 기획서의 3초 목표와 구분한다. 실제 목표 용량·인터넷 응답 검증은 아니다. `--subjects` 없는 메타데이터 점검이나 합성 부하는 출시 증거가 아니다. 부하 보고서는 입력 원문·호실 ID·접속 코드를 저장하지 않고 집계 수와 시간만 남긴다.

## 6. 평가 영수증과 비용

응답의 `receipt`에는 정규화 입력, 자료·정책 버전, 승인 구간, 모드, 시각이 있고 그 해시가 `assessment_id`다. 동일한 입력·자료·시각이면 동일한 ID이며 시각 변화는 최신성 결과를 바꿀 수 있다. 사용자별 평가 이력은 자동 영구 저장하지 않는다. 과거 재현에는 당시 공급 원문의 합법적 보관과 해당 정책 코드가 필요하다.

시장 검증 JSON에는 대상 호실·건물 ID, 역, 모집가격 등이 들어간다. 익명 공개 보고서로 부르지 않고 증거 폴더와 계약상 접근 범위 안에서 보관한다. 부하·유지관리 요약의 집계 전용 범위와 구분한다.

비용 API는 월세+관리비, 월 추가비, 개월, 입주 청구 총액, 그 안의 선납 월비용·환급성 보증금, 퇴거비·갱신비·할인을 명시적으로 받는다. 미상값을 0으로 대체하지 않는다.

`기간 비용 = (월세 + 관리비 + 월 추가비) × 개월 + (입주 청구 총액 − 선납 월비용 − 환급성 보증금) + 퇴거비 + 갱신비 − 할인`

보증금 전액 반환을 가정하며 반환되지 않는 금액은 별도 비용이다. 입주 현금은 청구 총액 그대로 표시하고 선납 월비용을 기간 비용에 중복 계산하지 않는다. 이 합계가 비교 엔진의 월세+관리비 기준을 바꾸지 않는다.

## 7. 저장·백업·복구·보존 정리

운영 저장 진입점은 `backend.v2.storage.open_store`이며 영속 로컬 SQLite만 받는다. PostgreSQL URL·네트워크 공유·메모리 DB·다중 인스턴스는 지원하지 않는다. 현재 원문과 재전송 방지용 과거 해시 영수증을 보관하며 모든 과거 원문을 자동 아카이브하지 않는다. 검증용 시점별 증거는 계약이 허용하는 별도 묶음으로 보존한다.

```powershell
python scripts/maintain_v2_store.py backup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --retention-days 7
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --retention-days 7
```

백업은 SQLite 온라인 백업 API, 원문·스키마·기한 검증, SHA256 매니페스트를 사용한다. 기본 7일은 개발 가정이며 계약의 더 짧은 기한이 우선한다. 정리는 기본 계획 출력이고 `--apply`를 명시해야 삭제한다. Phase 7.3부터 **backup·cleanup·restore에 `--suppliers .runtime/config/suppliers.json`을 전달하면 현재 계약 설정도 검사한다.** 조기 철회·비활성·만료·증빙 변경·공급원 제거가 반영되며 잘못된 설정이나 조사할 수 없는 백업이면 삭제 전에 거절한다. `--revoke-source`로 명시한 대상도 합산한다. 설정 검사와 파일 작업 전체를 원자적으로 잠그지는 않으므로 계약을 변경할 때 수집/유지보수 작업을 함께 중지하는 절차를 따른다. `--suppliers` 생략 시 원문 권리·명시적 철회만 적용하므로 조기 권리 종료를 각 명령에 `--revoke-source`로 전달해야 한다.

```powershell
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --revoke-source 실제-source-id --apply
python scripts/maintain_v2_store.py backup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --revoke-source 실제-source-id
```

복구에도 `--revoke-source 실제-source-id`를 붙인다. 철회된 공급원이 들어 있는 백업의 복구는 거절된다.

```powershell
python scripts/maintain_v2_store.py restore --manifest .runtime/backups/실제-매니페스트.manifest.json --destination .runtime/restored-v2.sqlite3
```

복구 목적지는 존재하지 않는 새 경로여야 한다. 해시·기한·권리를 다시 검사하며 운영 DB를 덮어쓰지 않는다. 운영 교체는 서버를 정지하고 검증된 새 DB 경로로 설정을 바꾸어 재시작한다. 만료·철회된 원본을 되돌리기 대상으로 사용하지 않는다.

자동 삭제 일정·원격 보관·호스트 백업·외부 경보 계정은 등록하지 않았다. 운영 일정은 계약의 삭제 기한에 맞춰 따로 구성한다. DB와 백업이 같은 디스크면 디스크 고장 대응이 아니며 물리적 완전 삭제도 보장하지 않는다. [STORAGE_LIFECYCLE_V2.md](STORAGE_LIFECYCLE_V2.md)에 상세 범위와 실패 처리가 있다.

## 8. 검증 상태

2026-09-13 전체 테스트: **356 passed, 1 skipped, 61 subtests**. 고정·합성 자료와 루프백 HTTP의 구현 검증 결과다. 실제 공급 계약, 30일 현장 관찰, 전문가 시장 검수와 공개 운영 성능을 대체하지 않는다.

위 숫자는 Phase 6~7 기반 개발 시점의 기록이다. 후속 Phase 7.3에서는 Docker 이미지 빌드·컨테이너 실행·재기동·저장 보존·복구·로컬 알림을 실제 검증했다. 최신 검사 수와 결과는 [단계별 진행표](../DEVELOPMENT_PHASES.md)를 따른다. [PILOT_DEPLOYMENT_V2.md](PILOT_DEPLOYMENT_V2.md)에 로컬/외부 공개 차이와 남은 배포 준비를 기록한다.

로컬 장애 기록은 `scripts/check_v2_alerts.py`로 한 번씩 확인한다. 별도 알림 SQLite에 고정 코드와 발생·복구 이력을 남기고 같은 장애의 중복 이벤트를 억제한다. 운영 매물/수집 DB는 읽기만 하며 외부 전송이나 반복 실행 일정은 등록하지 않는다. 명령·경로 충돌 방지·합성 훈련 범위는 [ALERTS_V2.md](ALERTS_V2.md)를 따른다.
