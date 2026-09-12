# 공급원 연결과 수집 작업 운영

이 문서는 Phase 6~7에서 구현한 **허가된 정규 JSON 피드**의 반입·재시도·감시 절차다. 공급 계약이나 실제 데이터가 생겼음을 의미하지 않는다. 저장소의 `config/suppliers.example.json`은 빈 목록이며 자동으로 외부 요청을 보내지 않는다. 공급원에 연락하거나 유료 서비스를 가입하는 동작도 없다.

현재 요구사항은 무료 운영을 우선한다. Python 표준 라이브러리와 로컬 SQLite로 수집기를 실행할 수 있다. 무료로 열람 가능한 웹페이지가 재수집·저장·비교·재표시까지 허용한다는 뜻은 아니다. 권한 있는 무료 JSON/CSV 원본이 생기면 공급자가 합의한 필드 의미에 따라 `DATA_IMPORT_V2.md`의 완결된 JSON으로 변환해야 한다. 이 수집기는 특정 포털의 비공개 API, 스크래핑, 로그인 우회 기능을 제공하지 않는다.

## 1. 실제 공급원의 시작 조건

1. 제공자와 자료 범위, 갱신 주기, 모집 상태 확인 시각, 정규 호실·건물·역 ID의 의미를 확인한다.
2. 비교·화면 표시·저장 허용을 확인할 수 있는 실제 계약/서면 허가 증빙을 운영자 전용 폴더에 보관한다. 이메일을 증빙으로 쓰려면 실제 수신한 내용을 파일로 저장한다. 저장소가 예시 증빙을 대신 만들지 않는다.
3. 증빙의 SHA-256을 계산하고 계약 만료, 조기 취소 여부와 함께 운영 설정에 기록한다. 해시 일치는 파일이 바뀌지 않았다는 확인이며 계약의 법적 효력을 자동 판정하지 않는다.
4. `complete:true`의 공급원 전체 스냅숏을 확보한다. 공급 API가 페이지 단위라면 모든 페이지 수신과 완전성을 확인하는 공급원별 변환기가 별도로 필요하다.
5. 일회 반입과 품질 검증을 통과한 뒤 해당 피드만 활성화한다. 실거래/성약가격과 모집가격을 혼용하지 않는다.

`agreement_path`는 공급원 설정 파일의 디렉터리 아래 상대 경로여야 한다. 심볼릭 링크 등으로 디렉터리 밖을 가리키는 경로도 거절한다. 운영 설정과 증빙은 웹 공개 폴더·Git 저장소에 보관하지 않고 운영자 계정만 수정할 수 있게 설정한다. 권리 확인은 반입 직전과 서비스의 읽기 시점에 반복한다.

## 2. 설정 형식

아래는 **형식 설명**이다. 실제 공급원 ID·증빙·날짜로 교체하고 권한을 확인하기 전에는 `enabled:false`를 유지한다. `suppliers`는 최대 100개다.

```json
{
  "schema_version": "2.0",
  "suppliers": [{
    "source_id": "replace-with-authorized-source-id",
    "enabled": false,
    "interval_seconds": 900,
    "max_attempts": 5,
    "allow_empty": false,
    "contract": {
      "agreement_path": "agreements/actual-permission.txt",
      "agreement_sha256": "0000000000000000000000000000000000000000000000000000000000000000",
      "comparison": false,
      "display": false,
      "storage": false,
      "expires_at": "2026-09-13T00:00:00Z",
      "revoked": false
    },
    "feed": {"type": "local", "path": "incoming/complete-snapshot.json"}
  }]
}
```

`source_id`는 스냅숏의 공급원 ID와 정확히 같아야 한다. 영문·숫자·마침표·밑줄·콜론·하이픈, 최대 100자를 허용한다. `interval_seconds`는 60~86400초, `max_attempts`는 1~10이다. 알 수 없는 설정 키, 중복 JSON 키, 비정상 날짜와 비정수 설정은 거절한다. 원문 스냅숏의 권리 만료가 계약 만료보다 뒤이면 실패하며, 수집기가 원문 날짜나 권리를 바꿔 맞추지 않는다.

로컬 파일은 운영자가 직접 지정한 절대 경로 또는 설정 폴더 기준 상대 경로다. UNC/네트워크 공유·URL·SQLite URI는 로컬 파일 경로로 받지 않는다. 파일 제공자는 임시 파일에 쓰기를 완료한 뒤 원자적으로 교체해야 한다. 읽는 중인 미완성 JSON은 반입하지 않는다.

실제 허가된 HTTPS JSON 피드가 있다면 `feed`만 다음 형식으로 바꾼다. 아래 주소는 형식 설명용 `.invalid`이며 서비스 엔드포인트가 아니다.

```json
{
  "type": "https",
  "url": "https://authorized-feed.invalid/snapshots/current.json",
  "allowed_origin": "https://authorized-feed.invalid",
  "auth_env": "HOUSE_EVALUATOR_FEED_TOKEN"
}
```

HTTPS 기본 포트 443, 정확히 허용한 origin, JSON 응답만 지원한다. 인증이 필요 없으면 `auth_env`를 생략한다. 인증이 필요하면 운영 프로세스에 해당 환경 변수의 값을 전달한다. 키 이름만 설정 파일에 넣고 값은 넣지 않는다. 인증 방식은 `Authorization: Bearer`다. URL 사용자 정보·쿼리·프래그먼트, 리디렉션, 프록시와 압축 응답은 지원하지 않는다. 다른 인증/페이지 처리 방식은 실제 공급 계약의 명세를 확보한 뒤 별도 어댑터로 구현한다.

DNS 응답 중 하나라도 사설·루프백·링크 로컬·비공개 주소이면 거절한다. 확인한 공개 IP로 직접 연결하고 원래 호스트 이름으로 TLS 인증서를 검증하여 DNS 재해석으로 다른 주소에 접속하지 않는다. DNS 작업은 5초, 요청은 약 30초 제한이며 본문은 최대 20 MiB다. 절대 시각 감시기가 소켓을 종료하므로 헤더나 본문을 조금씩 계속 보내는 응답도 무한정 기다리지 않는다. 공급 주소가 바뀌면 자동으로 따라가지 않고 운영자가 허용 origin을 검토한다.

## 3. 실행

저장소 루트에서 실행한다. 다음 명령은 빈 예시 설정을 사용하므로 아무 공급원도 조회하지 않는다.

```powershell
python scripts/run_v2_ingestion.py --config config/suppliers.example.json --db .runtime/v2.sqlite3 --state-db .runtime/supply.sqlite3 --once
```

`--once`가 기본값이다. 공급원별로 지금 실행할 시간이 된 작업을 한 차례 처리하고 종료한다. 실제 운영 설정을 만든 뒤 계속 실행하려면 운영자가 다음 프로세스를 명시적으로 시작한다.

```powershell
python scripts/run_v2_ingestion.py --config .runtime/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/supply.sqlite3 --worker --poll-seconds 10
```

이 명령은 실행 중인 CLI 프로세스이며 Codex 앱 예약 작업을 만들지 않는다. `Ctrl+C`로 종료한다. 작업 상태와 다음 실행 시각은 DB에 남는다. 재시작 후 예정된 작업부터 이어간다. 현재 지원 저장소는 무료 단일 호스트 SQLite이며 PostgreSQL DSN은 거절한다.

상태만 읽는 명령은 네트워크 요청·반입·DB 생성을 하지 않는다.

```powershell
python scripts/run_v2_ingestion.py --state-db .runtime/supply.sqlite3 --status
```

종료 코드 0은 실행 과정이 정상 종료되었다는 뜻이다. 일회 실행의 반입 실패 또는 `needs_attention`/`unavailable` 상태는 2다. 비활성·권리 문제로 시도하지 않은 공급원도 있으므로 출력의 `monitor.status`와 각 `contract_status`를 확인한다. `--worker`는 문제 해결을 기다리며 살아 있을 수 있고, 로그를 출력했다고 실제 시장 자료가 공급되었다는 뜻은 아니다.

## 4. 중복 실행·장애·재시도

SQLite 작업 DB에 공급원별 lease를 120초 동안 잡는다. 같은 상태 DB를 사용하는 다른 수집기가 같은 공급원을 동시에 수신하지 않는다. 수신 후 import 직전 lease와 현재 계약을 다시 확인한다. lease를 잃은 늦은 응답은 반입하지 않는다. 프로세스가 중단되면 lease가 끝난 뒤 다른 실행이 작업을 이어받을 수 있다.

실패 후 30초·60초·120초 순으로 재시도하며 최대 지연은 3600초다. `max_attempts`에 도달하면 `retry_exhausted:true`, `next_due:null`로 멈춘다. 설정/권리 상태 변경은 새로운 조건으로 재시도를 시작한다. 같은 설정에서 원인을 해결한 뒤 운영자가 다음처럼 명시적으로 재개할 수 있다.

```powershell
python scripts/run_v2_ingestion.py --config .runtime/suppliers.json --db .runtime/v2.sqlite3 --state-db .runtime/supply.sqlite3 --resume-source actual-source-id --once
```

재시도 포기·파싱 실패·스키마 변경·오래된 스냅숏·모집 상태 누락·권리 초과는 기존 정상 원문을 덮어쓰지 않는다. 저장소 자체가 허용하지 않은 합성 반입은 수집기에서 항상 금지한다. 완결된 빈 스냅숏은 실제로 전체 모집 종료를 확인한 공급원에 대해서만 `allow_empty:true`로 허용한다.

HTTP 304 또는 같은 스냅숏의 재수신은 `last_success`에 통신/처리 성공을 기록할 수 있지만, `snapshot_as_of`나 매물의 `status_verified_at`를 현재 시각으로 바꾸지 않는다. 24시간이 지나면 기존 비교 게이트에서 제외된다. 반입과 작업 상태 커밋 사이에 중단된 경우에도 스냅숏 ID의 멱등 반입과 저장소 현재 원문 조회로 날짜를 복구한다.

현재 이용 가능한 스냅숏이 없는 상태의 304는 실패로 취급한다. 보존 절차에서 원문을 삭제한 뒤 기존 스냅숏 ID를 재수신해도 정상 자료가 있는 것으로 보고하지 않는다. 과거 성공 저널은 실제 저장소 존재를 대신하지 않는다. 이전 스냅숏 원문의 권리 만료는 저장소에서 확인하고, 설정의 조기 취소·권한 제거·증빙 변경은 Runtime의 `permitted_source_ids()` 필터로 기존 자료도 즉시 사용 중단한다. **공급원 설정 파일을 서버에 연결해야 이 조기 취소 필터가 작동한다.** 수집기의 정상 실행만으로 다른 서버 설정을 자동 변경하지 않는다. 서버 운영 문서의 `HOUSE_EVALUATOR_SUPPLIERS`와 상태 DB 설정을 함께 적용한다.

## 5. 관측 가능성과 한계

`monitor_status(state_db, *, now=None)`는 읽기 전용 함수다. 응답에는 공급원 ID, 마지막 시도/성공, 다음 실행, 실패 횟수와 고정 오류 코드, 계약 상태·만료, 스냅숏 및 모집 상태 확인 시각, 최신성, lease 상태가 있다. 예정 시각을 60초 넘겨도 실행 중인 lease가 없으면 `worker_overdue:true`로 주의를 요구한다. `last_success`는 시세 최신성을 의미하지 않는다. `freshness`는 상태 시각 범위로 `fresh`, `mixed`, `stale`, `unknown`을 구분한다. 개별 매물의 충분한 조건과 모집 상태는 비교 엔진에서 따로 검증한다.

증빙/설정 변경에 대한 모니터의 계약 상태는 수집기가 마지막으로 확인한 상태다. 시간에 따른 계약 만료는 모니터 조회 때 다시 적용한다. 실제 서비스에서 권리 판단은 설정 파일과 증빙을 다시 읽는 필터가 맡는다. 수집기 정지 상태에서 표시된 이전 성공을 운영 정상으로 해석하지 않는다.

`supply_journal`은 공급원 ID·시각·고정 이벤트 코드·시도 횟수만 저장한다. 최근 1000개 이벤트를 보관한다. URL·토큰·증빙 경로·오류 원문·공급 매물 원문은 이 저널에 저장하지 않는다. 로컬 SQL 읽기 권한이 있는 운영자가 조사할 수 있으며 외부 이메일/메신저 알림을 보내지 않는다. 수집기 출력도 같은 안전한 정보만 포함한다.

작업 큐는 **단일 호스트 로컬 SQLite 파일**에서 동작한다. 작업 큐를 여러 호스트에 복사하면 전역 lease를 보장하지 않는다. 이 구성에서는 수집기 호스트를 하나로 유지한다. 여러 수집 호스트가 필요할 때 공유 데이터베이스 기반 lease와 장애 전환 시험을 추가한다. 계약별 보존/삭제·백업 복원은 저장소 운영 절차와 함께 확인한다.

## 6. 오프라인 검증

```powershell
python -m pytest backend/tests/test_v2_supply.py -q
```

테스트는 실제 외부 피드에 접속하지 않는다. 로컬 반입·일정 복원·lease 경쟁·권리 취소·원문 보존·재시도 포기·오류 정보 가림·DNS 사설 주소·리디렉션 차단·TLS 호스트 보존·304 시각 불변을 검증한다. 이 결과는 수집 시스템의 동작 증거이며 공급 권리나 실제 시세 정확도의 증거가 아니다.
