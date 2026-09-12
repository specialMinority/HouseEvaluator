# 로컬 장애 기록과 알림 훈련

이 도구는 기존 PC에서 공급·매물 상태를 한 번 검사하고, 별도 SQLite 파일에 장애 발생·복구 이력을 저장한다. 외부 메시지 전송, 반복 실행 예약, 공개 승인 변경은 수행하지 않는다. 사용권을 확보한 실제 공급사와 서버가 없는 현재 단계에서는 로컬 운영 동작을 검증하는 용도다.

## 상태 검사

저장소 루트에서 실행한다. 예시 공급 설정은 비어 있으므로 정상적으로 `needs_attention`과 공급 미설정·실매물 부재를 표시한다.

```powershell
python scripts/check_v2_alerts.py --db .runtime/v2.sqlite3 --state-db .runtime/ingestion.sqlite3 --alerts-db .runtime/alerts.sqlite3 --suppliers .runtime/config/suppliers.json --output .runtime/reports/alerts-status-001.json
```

- `--db`, `--state-db`: 읽기 전용 검사 대상이다. 파일이 없으면 생성하지 않는다.
- `--alerts-db`: 별도 장애 기록 파일이다. 없으면 새로 생성한다. 같은 파일을 다시 사용해야 중복 장애가 억제된다.
- `--suppliers`: 현재 계약·증빙·권한을 검사할 로컬 설정이다. 미지정, 무효, 만료, 철회 상태를 실매물 사용 가능 상태로 해석하지 않는다.
- `--output`: 선택 사항인 새 JSON 파일이다. 이미 존재하는 결과는 덮어쓰지 않는다.
- 종료 코드: `0`은 현재 검사한 장애 없음, `1`은 활성 장애 있음, `2`는 경로·기록 파일 등 검사 실행 실패다. `0`은 공개 가능 여부나 시장 정확성 승인이 아니다.

검사 범위는 현재 사용 가능한 실제 매물, 활성 매물의 24시간 이내 상태 확인, 수집 성공·실패·지연 및 계약 상태다. 공급·매물 DB는 SQLite 읽기 전용 연결로 연다. 공개 승인 조건 전체는 `check_v2_release.py` 또는 실행 서버의 `/api/v2/readiness`에서 별도로 확인한다. 네트워크 포트·브라우저 정상 작동까지 검사하는 도구는 아니다.

## 기록 형식과 중복 처리

`local-alert-status-1.0` 결과에는 현재 장애, 새 발생·복구 이벤트, 확인 시각, 집계 건수와 코드·설정 해시만 포함된다. 공급사명, 매물 식별자·가격, 로컬 경로, URL, API 키, 원본 오류 문자열은 출력하지 않는다.

`opened`는 장애가 처음 발생했을 때, `recovered`는 다음 검사에서 해당 장애가 사라졌을 때 한 번 기록된다. 같은 상태를 재검사하면 `new_event_count`는 `0`이다. 프로그램을 종료하고 다시 실행해도 이 규칙이 유지된다. 여러 장애 중 일부만 사라지면 해당 장애의 복구만 기록된다. 검사 시각이 이전 기록보다 과거이면 상태 변경을 거부한다.

기록 파일은 현재 활성 장애와 최근 이벤트 최대 1,000건을 보관한다. 여러 공급사에서 같은 종류의 장애가 발생하면 하나의 코드로 합쳐진다. 개별 공급사 진단은 기존 수집 상태 도구에서 확인한다. 잘못된 스키마의 기존 DB, 서로 같은 파일·하드링크·SQLite 보조 파일 경로, 심볼릭 링크·정션 경로는 거부한다. POSIX 새 파일 권한은 `0600`이며 Windows에서는 저장 디렉터리의 ACL도 적용되므로 개인 계정 전용 `.runtime` 폴더로 관리한다.

| 오류 코드 | 의미 |
|---|---|
| `supplier_not_configured` | 사용 가능한 공급 계약 없음 |
| `supplier_configuration_invalid` | 설정을 읽거나 검증할 수 없음 |
| `supplier_contract_invalid` | 활성 공급사의 계약·증빙·권한이 유효하지 않음 |
| `snapshot_unavailable` | 매물 DB를 읽을 수 없거나 허용 공급사의 현재 스냅샷이 없거나 스냅샷 사용권 만료 |
| `snapshot_invalid` | 저장된 매물 해시·구조 검증 실패 |
| `market_data_unavailable` | 현재 허용된 활성 실제 매물 없음 |
| `listings_stale` | 활성 실제 매물 중 상태 확인이 없거나 24시간 초과 |
| `worker_unavailable` | 수집 상태를 읽을 수 없음 |
| `worker_not_configured` | 공급 설정 또는 필요한 수집 상태 없음 |
| `worker_failed` | 수집 실패·재시도 소진 상태 |
| `worker_overdue` | 수집 예정 시각 지연 또는 최근 성공 없음 |
| `worker_contract_invalid` | 수집 상태에 유효하지 않은 계약 기록 |

## 장애·복구 훈련

```powershell
python scripts/drill_v2_alerts.py --suppliers .runtime/config/suppliers.json --output .runtime/reports/alerts-drill-001.json
```

임시 DB에서 정상 → 장애 2건 → 프로그램 재시작 후 중복 억제 → 부분 복구 → 전체 복구 → 재검사까지 수행한다. 실제 DB나 수집 자료를 변경하지 않으며 임시 파일은 훈련 종료 때 삭제한다. 결과는 새 파일로 보관하고 실제 전달 검증으로 오해하지 않는다.

보고서 형식은 `operations-report-1.0`, `kind=alerts_drill`, **`scope=synthetic_smoke`로 고정**된다. `passed=true`는 로컬 발생·복구 기록 검증 통과를 의미한다. `external_delivery_verified=false`, `human_receipt_verified=false`, `evidence_registered=false`를 함께 기록한다. 코드·설정 해시는 같은 구현과 공급 설정을 식별하지만 실제 공급·전달 증거를 대신하지 않는다.

Phase 7.3의 실제 장애 알림 완료에는 운영 환경에서 전송 경로를 정하고, 담당자가 전달·복구 알림을 실제로 받았다는 확인이 필요하다. 아직 전송 경로를 만들거나 누구에게도 메시지를 보내지 않았다. 이 합성 보고서를 공개 승인 자료로 등록해도 현재의 `market_operations` 조건을 통과하지 못한다.

## 현재 제한

검사 프로그램을 실행하지 않는 동안은 새 장애를 감지하지 못한다. PC 자체의 전원·네트워크·디스크 장애는 같은 PC의 프로그램만으로 외부에 알릴 수 없다. 상시 운영을 시작하기 전 별도 실행 주기, 외부에서 보는 생존 확인, 수신 담당자와 전달 경로를 정해야 한다. 이 문서의 명령은 반복 작업을 자동 등록하지 않는다.
