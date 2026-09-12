# 무료 우선 실행과 제한 공개 준비

현재 선택은 기존 PC 한 대에서 Python 표준 라이브러리와 SQLite로 실행하는 방식이다. 서버·수집 작업·검증·백업에 별도 유료 서비스가 필요하지 않다. PC 가동, 전기·통신 비용과 데이터 이용료까지 무료라는 의미는 아니다. 실제 공급 계약은 아직 없으며 무료로 확보할 수 있는지도 미정이다. 클라우드 계정·리소스·도메인·외부 알림은 생성하지 않았다.

소프트웨어 준비 상태와 실제 시장 출시 상태를 구분한다. 지금은 빈 공급 설정 또는 합성 자료로 기능을 확인할 수 있다. 실제 공급 계약, 30일 관찰, 전문가 검수, 운영 훈련과 공개 승인 전에는 `ready=false`가 정상이다.

## 1. 로컬 실행 구성

저장소 루트에서 실행한다. Python 3.12 이상이 필요하다. `.runtime/`은 Git에서 제외되고 HTTP 정적 경로로 제공되지 않는다. 다음 초기화는 기존 설정을 덮어쓰지 않는다.

```powershell
New-Item -ItemType Directory -Force .runtime/config | Out-Null
if (-not (Test-Path .runtime/config/suppliers.json)) {
    Copy-Item config/suppliers.example.json .runtime/config/suppliers.json
}
if (-not (Test-Path .runtime/config/registry.json)) {
    Copy-Item docs/validation_registry.example.json .runtime/config/registry.json
}
if (-not (Test-Path .runtime/config/release.json)) {
    Copy-Item config/release_evidence.example.json .runtime/config/release.json
}
$env:HOUSE_EVALUATOR_DB = '.runtime/v2.sqlite3'
$env:HOUSE_EVALUATOR_SUPPLIERS = '.runtime/config/suppliers.json'
$env:HOUSE_EVALUATOR_INGESTION_STATE = '.runtime/ingestion.sqlite3'
$env:HOUSE_EVALUATOR_VALIDATION_REGISTRY = '.runtime/config/registry.json'
$env:HOUSE_EVALUATOR_RELEASE_EVIDENCE = '.runtime/config/release.json'
$env:HOUSE_EVALUATOR_DEMO = '0'
$env:HOUSE_EVALUATOR_LEGACY = '0'
$env:HOUSE_EVALUATOR_PILOT = '0'
python -m backend.src.server
```

접속 주소는 `http://127.0.0.1:8000/frontend/v2/`이다. 데이터 없는 실제 모드는 판단을 보류한다. 화면 시연은 서버 시작 전 `HOUSE_EVALUATOR_DEMO=1`을 명시하고 화면에서 합성 시연을 선택한다. 데모는 실제 자료와 혼합되지 않는다.

접속 보호가 필요하면 시작 전 아래처럼 코드를 생성한다. 생성값을 명령줄 인자·URL·커밋·스크린샷에 넣지 않는다. 초대자는 같은 접속 코드를 공유하며 계정별 권한이나 사용자별 취소 기능은 없다. 코드를 교체하고 서버를 재시작하면 기존 코드로 접속할 수 없다.

```powershell
$env:HOUSE_EVALUATOR_ACCESS_TOKEN = python -c "import secrets; print(secrets.token_urlsafe(32))"
$env:HOUSE_EVALUATOR_REQUESTS_PER_MINUTE = '120'
python -m backend.src.server
```

접속 코드는 32~256자 ASCII이며 모든 `/api/` 요청에 Bearer 인증을 적용한다. 브라우저는 메모리에만 보관하며 새로고침·코드 삭제로 지운다. 요청 한도는 실제 TCP 접속 IP 기준이고 `X-Forwarded-For`를 신뢰하지 않는다. 프록시 뒤 여러 이용자는 같은 한도를 공유할 수 있다. 인터넷 제공에는 TLS 종단·접근 범위·접속 로그 보관과 실제 부하 검토가 추가로 필요하다. 로컬 바인딩을 인터넷 공개로 바꾸는 작업은 아직 수행하지 않았다.

## 2. 공급원이 없는 상태에서 수집기 점검

```powershell
python scripts/run_v2_ingestion.py --config .runtime/config/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/ingestion.sqlite3 --once
python scripts/run_v2_ingestion.py --state-db .runtime/ingestion.sqlite3 --status
```

예제는 `suppliers=[]`이므로 외부 요청 없이 `not_configured`를 반환한다. 공급 확보 후에는 [공급 운영 문서](SUPPLY_OPERATIONS_V2.md)에 따라 계약 근거 파일·SHA256·비교/표시/보관권·만료·철회와 피드를 설정한다. 정규화 JSON의 로컬 파일 또는 허용된 HTTPS 원점만 지원한다. 공급사의 원래 스키마가 다르면 별도 정규화 어댑터와 실제 호실·건물·역 매핑이 필요하다.

```powershell
python scripts/run_v2_ingestion.py --config .runtime/config/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/ingestion.sqlite3 --worker
```

수집 작업은 별도 터미널에서 실행하고 `Ctrl+C`로 종료한다. 이 명령은 자동 일정이나 PC 시작 작업을 등록하지 않는다. SQLite에 공급원별 실행권·재시도·백오프·중단 상태를 보존한다. 인증정보는 공급 설정에 지정한 환경변수에서 읽는다. 원본 API 키나 계약 파일을 공개 저장소에 넣지 않는다.

공급 정책이나 계약이 바뀌어도 계속 수집을 보장할 수는 없다. 변경은 해당 공급원에서 감지·중단하고 기존 자료의 실제 시각·권리를 유지한다. 304 응답과 같은 파일 재수신으로 모집 확인 시각을 갱신하지 않는다. 계약 철회는 다음 읽기와 수집에서 차단한다. HTTP 상태 API와 로컬 작업 기록은 구현했으며 외부 메시지 알림은 아직 연결하지 않았다.

## 3. 백업·복구·보관 정리

```powershell
python scripts/maintain_v2_store.py backup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --suppliers .runtime/config/suppliers.json
python scripts/drill_v2_restore.py --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --suppliers .runtime/config/suppliers.json --state-db .runtime/ingestion.sqlite3 --output .runtime/config/reports/restore-001.json
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --suppliers .runtime/config/suppliers.json
```

백업은 SQLite 온라인 백업 API를 사용한다. 복구 훈련은 임시 신규 경로에서 무결성·해시·수신 기록·개수를 확인한 뒤 임시 파일을 정리한다. 운영 DB를 덮어쓰지 않는다. `cleanup`은 기본적으로 계획만 출력하며 삭제하려면 확인한 명령에 `--apply`를 추가한다.

Phase 7.3에서 backup·cleanup·restore에 `--suppliers` 연결을 추가했다. 현재 허용되지 않은 관측 공급원은 조기 철회·권리 종료 대상으로 처리하며, 잘못된 설정은 삭제 전에 거절한다. 설정에 없는 합성 자료는 실제 공급원으로 취급하지 않는다. 원문 권리가 먼저 끝나면 그 기한도 계속 적용한다. 설정 변경과 유지관리를 동시에 수행하지 않는 절차는 [저장 수명주기 안내](STORAGE_LIFECYCLE_V2.md)를 따른다.

`--suppliers`를 생략하면 원문에 저장된 권리만 검사하므로 조기 계약 종료는 **backup·cleanup·restore 모두** `--revoke-source SOURCE_ID`로 전달해야 한다. 설정과 무관하게 추가 철회할 때도 이 옵션을 사용할 수 있다. 다음은 `SOURCE_ID`를 실제 ID로 바꾸어 실행할 예시다. 백업은 해당 자료가 남아 있으면 거절하므로 정리 계획과 실제 적용을 먼저 수행해야 한다.

```powershell
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --revoke-source SOURCE_ID
python scripts/maintain_v2_store.py cleanup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --revoke-source SOURCE_ID --apply
python scripts/maintain_v2_store.py backup --db .runtime/v2.sqlite3 --backup-dir .runtime/backups --revoke-source SOURCE_ID
```

설정에서 읽기 차단한 것만으로 디스크에서 삭제되는 것은 아니다. `drill_v2_restore.py --suppliers ...`는 공급 설정도 확인하므로 철회된 자료가 남으면 훈련을 거절한다.

기본 현재 스냅숏·백업 보관 상한은 7일이며 이용권이 더 일찍 끝나면 그 시각이 우선한다. 작업 상태 DB, 계약 증빙, 검증용 과거 스냅숏과 보고서는 이 정리 범위에 포함되지 않으므로 각각 보관 권한과 절차가 필요하다. 30일 시장 검증에는 별도 허가된 과거 자료 보관이 필요하다. 실제 운영 일정, 외부 사본, 장비 장애에 대한 복구는 아직 구성하지 않았다. 자세한 범위와 신규 경로 복구 명령은 [저장 수명주기 문서](STORAGE_LIFECYCLE_V2.md)를 따른다.

## 4. 시장 검증과 승인

[시장 검증 문서](MARKET_VALIDATION_V2.md)에 맞춰 사전 고정한 시간·건물 분리 요청셋, 허가된 시점별 스냅숏, 전문가 검수와 공급원별 연속 30일 기록을 준비한다.

```powershell
python scripts/validate_v2_market.py .runtime/evidence/manifest.json --output .runtime/config/reports/market-001.json
```

합성 입력의 `--smoke` 보고서는 시장 승인을 받을 수 없다. 실제 보고서도 모든 기준을 통과한 도시×평면만 검토 대상이다. JSON 내용 SHA256·현재 비교 정책 해시·공급원 집합·개별 기준·시점·운영자 승인을 확인하며 CLI가 승인 등록부를 자동 변경하지 않는다. 모집가격 대비 오차는 적정 가격 또는 실제 계약가격의 정답 검증과 다르다.

검증 보고서에는 호실·건물 식별자와 가격이 포함될 수 있다. 계약·원자료와 함께 비공개 증거로 보관한다. 화면에 표시 가능한 공급권이 검증 보고서 전체를 공개할 권한을 뜻하지 않는다.

## 5. 부하 측정과 공개 준비 검사

부하 측정기는 loopback 서버만 대상으로 한다. 실제 공급 자료로 작성한 Subject 배열 파일을 사용하고 최소 10개 호실·5개 건물·60회 요청·동시성 4를 기준으로 한다. 데모 또는 기본 메타데이터 요청 측정은 실제 운영 증거가 될 수 없다.

```powershell
python scripts/load_test_v2.py --url http://127.0.0.1:8000 --subjects .runtime/evidence/load-subjects.json --mode market --requests 60 --concurrency 4 --output .runtime/config/reports/load-001.json
python scripts/check_v2_release.py --db .runtime/v2.sqlite3 --suppliers .runtime/config/suppliers.json --state-db .runtime/ingestion.sqlite3 --registry .runtime/config/registry.json --evidence .runtime/config/release.json --output .runtime/reports/readiness-001.json
```

보고서 출력은 새 파일 경로만 허용한다. 준비 검사의 종료 코드는 준비 완료 `0`, 미충족 `2`, 실행 오류 `1`이다. 이 CLI는 데모·레거시를 끈 로컬 실행 구성을 검사한다. 실행 중인 서버의 설정을 조회하지 않으므로 실제 서버 확인은 인증된 `GET /api/v2/readiness`를 사용한다. API는 준비 완료 `200`, 미충족 `503`과 항목별 결과를 반환한다.

`release.json`은 승인된 운영 증거의 상대 경로와 SHA256을 지정한다. 위 예시는 `.runtime/config/reports/`에 생성하므로 설정 파일에서 `reports/load-001.json`처럼 참조한다. 시장 승인 `registry.json`도 같은 폴더 제한을 적용한다. `../evidence/`처럼 설정 폴더 밖의 경로는 거절한다. 내용 해시는 `Get-FileHash .runtime/config/reports/load-001.json -Algorithm SHA256`으로 확인해 소문자로 기록할 수 있다. 운영 증거는 코드·공급 설정 fingerprint도 현재와 같아야 한다.

요구되는 운영 보고서 종류는 `restore_drill`, `load_test`, `alerts_drill`, `usability_review`다. 보고서는 `scope=market_operations`, `passed=true`, `kind`, `checked_at`, `context_fingerprint`를 갖고 실제 검수 근거를 포함해야 한다. 복구·부하·알림은 7일, 사용성은 30일 이내여야 한다. `synthetic_smoke`는 거절한다. 보고서 해시는 사후 변경 감지용이며 기록의 진위를 외부에서 보증하는 전자서명은 아니다.

외부 알림 전달 시험과 한국인 대상 사용성 검수는 아직 실행하지 않았다. 사실에 근거한 해당 보고서가 없으면 검사도 통과하지 않는다. `monthly_cost_yen=0`, 비용을 확인한 운영자 `budget_confirmed_by`, 별도 `operator_release_approved=true`가 필요하다. 실제 공급 비용이 발생하면 사용자와 예산 및 기준을 다시 정해야 한다.

`HOUSE_EVALUATOR_PILOT=1`은 강한 접속 코드·공급 설정·데모/레거시 비활성화를 시작 조건으로 요구하고 준비 미충족 시 평가 요청을 `503`으로 차단한다. 훈련은 로컬 개발 모드에서 먼저 수행하고, 근거를 검토한 뒤 제한 공개 모드를 켠다. 현재 설정 예시는 미승인 상태다.

## 6. 선택적 컨테이너 구성

이미 사용 가능한 Docker 환경이 있을 때만 선택한다. 컨테이너 없이 위 Python 명령으로 개발과 로컬 검증이 가능하다. 저장소의 `compose.yaml`은 API와 선택적 수집 작업을 한 호스트에서 실행하고 `127.0.0.1:8000`으로만 연결한다. 예제 설정 파일을 먼저 초기화한다.

```powershell
docker compose config --quiet
docker compose up --build -d api
docker compose --profile ingestion up -d ingestion
docker compose down
```

API는 512 MiB/1 CPU, 수집 작업은 256 MiB/0.5 CPU 제한을 설정했다. 비루트 사용자, 읽기 전용 컨테이너 루트, 설정 읽기 전용 마운트, 데이터·백업 볼륨을 사용한다. 메모리·CPU 값은 실제 공급량을 측정하기 전의 초기 제한이며 성능 보장이 아니다. 환경변수 기반 공급 인증을 쓰려면 해당 변수만 수집 서비스에 명시적으로 전달하는 비공개 Compose 확장 설정이 필요하다. 상대 피드·계약 파일은 컨테이너에서 읽을 수 있는 `/config` 아래에 배치하고 OS 권한도 확인한다.

Phase 7.3에서 Docker 엔진을 기동하고 실제 이미지 빌드·비루트/읽기 전용 실행·생존 검사·강제 종료 복구·재생성 뒤 저장 내용 보존·복구/로컬 알림 훈련을 통과했다. 로컬 포트는 기본 8000이며 `HOUSE_EVALUATOR_PORT`로 변경할 수 있다. `down`은 기본적으로 데이터 볼륨을 보존한다. 아래 별도 리허설은 고유한 프로젝트·이미지·볼륨에 합성 자료만 생성하고 끝나면 그 임시 자원만 정리한다.

```powershell
python scripts/rehearse_v2_pilot.py --output-dir .runtime/reports/pilot-rehearsal-001
```

출력 폴더는 새 경로를 사용한다. Docker 명령 실패가 나면 로컬 `diagnostics.txt`를 확인한다. 외부 공개/TLS 설정 예제와 아직 필요한 도메인·회선 조건은 [배포 검수 문서](PILOT_DEPLOYMENT_V2.md), 실제 사용자를 대상으로 기록할 검수 항목은 [사용성 검수 양식](PILOT_USABILITY_V2.md)에 있다. 현재 외부 포트 연결·인증서 발급·사용자 모집은 수행하지 않았다.

## 다음 작업 순서

1. 허용된 공급 계약과 정규 ID·시각·삭제 의미를 확보하고 실제 스키마 어댑터를 검수한다.
2. 비용과 과거 자료 보관권을 확인한 후 공급 관찰·시장 검증을 진행한다.
3. 실제 자료로 복구·부하·장애 알림·사용성을 확인하고 공개 대상 구간과 가동 환경을 결정한다.
4. 준비 검사 통과와 운영자 승인 후 제한 공개한다. 공급량이 단일 SQLite 범위를 넘는다는 측정 근거가 생기면 저장소·작업 큐 확장을 설계한다.
