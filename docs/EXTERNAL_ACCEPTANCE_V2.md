# 외부 경보 수신과 실제 사용자 검수

작성 상태: **시행 전 계획**. 외부 알림 채널·수신자·참여자는 아직 정해지지 않았고 알림 전송이나 사용자 검수를 수행하지 않았다. 이 문서와 로컬 합성 훈련은 공개 통과 증거가 아니다. 공급권·시장 검증을 먼저 확보한 뒤 실제 시행 결과를 비공개로 기록한다. 이 절차는 향후 공급 계약을 사용하는 `market_operations` 공개 경로에 적용한다. 공급사 없이 사용자 입력 자료를 개인적으로 비교하는 버전의 출시 조건을 정의한 문서가 아니다.

## 단계 1. 시행 범위 고정

운영자는 공개할 도시×평면, 실제 허용 공급원, 실행 버전, 시험 시간과 담당자를 먼저 정한다. `/api/v2/readiness`에 나온 해당 실행의 `context_fingerprint`를 기록한다. 코드 또는 공급 설정을 바꾸면 새 fingerprint에서 필요한 훈련을 다시 수행한다.

경보 시험은 운영 자료를 망가뜨리지 않는 격리된 운영 복제 환경에서 실시할 수 있다. 복제·보관권도 유효해야 한다. 실제 공급이 없는 임의 fixture는 `synthetic_smoke`로 남긴다. 외부 전송은 사용자가 수신 채널·수신자·발송을 승인한 후에만 실행한다. 이 저장소의 `check_v2_alerts.py`와 `drill_v2_alerts.py`는 외부 발송기를 포함하지 않는다.

원자료와 보고서는 `.runtime/config/reports/` 등 비공개 경로에 둔다. 이름·전화번호·이메일·접속 코드·API 키는 보고서에 넣지 않고, 운영자가 별도로 대응표를 관리하는 익명 ID를 사용한다. 증빙 파일은 개인정보와 계약상 제한을 검토하여 최소 내용만 보관한다.

## 단계 2. 외부 경보 전달 확인

먼저 아래 빈 항목을 확정한다. 수신자가 있다는 가정이나 HTTP 성공 코드만으로 수신 확인을 기록하지 않는다.

| 사전 항목 | 시행 전에 기록할 값 |
| --- | --- |
| 운영자·수신자 ID | 실제 담당자의 익명 ID |
| 채널과 발송 승인 근거 | 수신 경로, 사용자 승인 일시·내부 기록 위치 |
| 월 추가 구독료와 한도 | 실제 가격·메시지 한도, 0원 유지 여부 확인 |
| 시험 실행 | 환경 ID, context_fingerprint, 실제 허용 source_ids |
| 시험 기준 고정 시각 | 첫 장애 주입 전 UTC 시각 |
| 수신 시간 기준 | 기본 제안: 발생·회복 각각 300초 이내, 시행 전에 확정 |
| 실패 때 조치 | 담당자, 채널 복구 및 재시험 순서 |

다음 순서로 장애와 회복을 한 쌍으로 검사한다.

1. 정상 상태에서 새 이벤트가 없는지 확인한다.
2. 허가된 시험 환경의 수집 작업을 중단하는 등 한 가지 장애를 주입한다. 실제 코드·시각·로컬 `event_id`를 기록한다.
3. 발송 결과 식별자와 전달 확인 증빙을 보관한다. 수신자가 자기 기기에서 해당 장애를 읽었음을 직접 확인한 시각과 익명 ID를 기록한다.
4. 상태가 계속 같을 때 반복 점검 및 점검 프로세스 재시작을 수행한다. 같은 장애 알림이 중복 전달되지 않는지 수신 이력과 함께 확인한다.
5. 장애를 해소하고 회복 이벤트를 확인한다. 별도 회복 알림의 전달 식별자와 수신자 확인을 남긴다.
6. 전송 실패와 채널 일시 중단을 시험해 실패가 기록되는지, 복구 후 미전달 이벤트가 지정한 정책대로 처리되는지 확인한다. 전달 성공으로 잘못 기록하지 않아야 한다.

수신 기록은 장애·회복 각각 한 행 이상 필요하다.

| event_id | code | event | occurred_at | delivered_at | recipient_id | acknowledged_at | 전달/수신 증빙 경로·SHA256 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 미실시 | — | opened/recovered | — | — | — | — | — |

통과 조건은 장애와 회복의 실제 외부 전달·사람 수신, 사전 확정 시간 기준, 재시작 후 중복 억제, 전송 실패 확인과 복구를 모두 충족하는 것이다. `opened`와 `recovered`의 이벤트 ID는 서로 달라야 하며 같은 장애 코드를 가리켜야 한다. 시각은 `occurred_at <= delivered_at <= acknowledged_at <= checked_at` 순서여야 한다. 로컬 로그 저장, 채널의 요청 수락, 최종 수신자 확인을 각각 구분한다.

최종 `alerts_drill` 보고서에는 공통 필드와 함께 아래의 실제 값·근거가 필요하다.

```text
schema_version: operations-report-1.0
kind: alerts_drill
scope: market_operations
checked_at / context_fingerprint / passed
delivery_scope: external
notification_sent / external_delivery_verified / human_receipt_verified
protocol_frozen_at / protocol_evidence / operator_id / source_ids
max_delivery_seconds: 사전 선언한 양의 정수, 최대 86400초
deliveries: 위 장애·회복 수신 기록
checks: incident_delivered / recovery_delivered / within_time_budget
        restart_deduplicated / unchanged_deduplicated / delivery_failure_recovered
evidence: 내부 기록 경로와 SHA256, 미완료 항목 및 제한
```

`deliveries`의 각 행에는 문자열 `event_id`, `incident_id`, `code`, `event`, `provider_receipt_id`, `recipient_id`, 위의 세 시각, `delivery_evidence`, `receipt_evidence`를 기록한다. 한 사건의 발생·회복은 같은 `incident_id`·`code`·수신자를 사용하고, 이벤트·전달 결과 식별자는 각각 달라야 한다. 시간 기준은 최종 사람 수신 확인까지 적용한다. 각 `checks` 값과 세 전달 확인 플래그는 엄격한 JSON boolean이어야 하며 `1`이나 문자열 `"true"`를 받지 않는다.

이는 채울 필드 목록이며 실행 가능한 통과 예시 JSON이 아니다. 현재 보고서의 false 값을 true로 바꾸거나 scope를 바꿔서 결과를 승격하지 않는다. 외부 채널 자체와 전송기 구현·운영·수신 시험이 모두 남아 있다.

## 단계 3. 한국어 사용자 관찰

상세 진행 질문은 [사용성 검수 양식](PILOT_USABILITY_V2.md)을 사용한다. 작은 제한 공개의 첫 검수 계획은 **한국어 대상 사용자 최소 3명, 일본 부동산 초보자 최소 2명, 실제 휴대폰 사용 최소 2명**을 제안한다. 이는 통계적 대표성을 주장하는 표본 수가 아니라 세 도시의 흐름과 초보자·휴대폰 문제를 빠뜨리지 않기 위한 운영 기준이다. 시행 전에 인원·도시·과제 배정을 확정하고 관찰 결과를 본 뒤 통과 기준을 낮추지 않는다.

참여자는 실제 사람이어야 하고 진행자 혼자 한 점검이나 에이전트 실행을 참여자로 세지 않는다. 참여자의 동의와 기록 보관 기간을 먼저 정한다. 연구용 개인 정보를 수집하지 않아도 익명 ID, 대상 조건, 거주 경험, 기기, 관찰 결과를 기록할 수 있다.

| 과제 ID | 필수 실제 관찰 |
| --- | --- |
| city_input_tokyo / city_input_osaka / city_input_fukuoka | 각 도시의 입력·수정 흐름. 아직 미승인 도시는 보류 흐름만 검수 |
| higher_price | 승인된 실제 구간에서 높은 모집가격 결과와 관리비 포함 기준을 설명 |
| lower_price | 승인된 실제 구간에서 낮은 모집가격 결과가 하자·입주 가능 보장이 아님을 설명 |
| abstention | 실제 자료 없음·오래됨·표본 부족의 보류를 이해 |
| synthetic_distinction | 별도 합성 시연임을 식별. 공개 모드에서는 시연 비활성도 확인 |
| evidence_relaxation | 비교 수·확인 시각·일치 조건·완화 조건과 불확실성을 설명 |
| initial_cost | 비용 미상·환급 가능 보증금·실제 지출의 차이를 설명 |
| duration_cost | 6/12/24개월 차이와 선납 월세를 중복 계산하지 않음을 설명 |
| mobile_access | 실제 휴대폰에서 접속 오류 회복·코드 삭제·근거 읽기 |

과제마다 익명 참여자 ID, 시작·종료 시각, 도시×평면, 실제/합성 구분, 완료·도움 필요·미실시, 직접 관찰한 행동·발언, 문제 영향과 수정·재검수 결과를 남긴다. 가격 방향 사례가 실제로 없으면 미실시로 기록한다. 임의로 가격을 바꾸어 필요한 방향을 만들지 않는다. `synthetic_distinction` 과제만으로 검수 전체가 합성이 되는 것은 아니지만, 실제 가격·근거 이해 과제는 승인된 실제 자료에서 시행해야 한다.

통과 조건은 사전에 정한 참여자·도시·기기·필수 과제의 수행, 핵심 오해를 만드는 공개 차단 문제 0건, 진행자 도움이 필요했던 필수 과제의 수정 후 재검수 완료다. 일부 과제만 시행하거나 관찰 기록이 없으면 전체 통과로 기록하지 않는다. 공개 차단 문제를 해결한 경우에도 수정 기록과 실제 사람의 재검수를 연결한다.

최종 `usability_review` 보고서에는 아래의 실제 값·근거가 필요하다.

```text
schema_version: operations-report-1.0
kind: usability_review
scope: market_operations
checked_at / context_fingerprint / passed
protocol_frozen_at / protocol_evidence / facilitator_id / reviewer_id
protocol: minimum_participants / minimum_mobile_participants / minimum_novice_participants
          required_cities / required_task_ids
approved_segments / source_ids
participants: participant_id / target_user / japan_housing_novice / devices
observations: observation_id / participant_id / task_id / city / device
              started_at / ended_at / data_kind / segment / result / notes / evidence
issues: issue_id / original_observation_id / severity / resolved / resolution
        resolution_evidence / retest_observation_ids
open_blocker_count / planned_tasks_completed / human_participants_verified
limitations / evidence: 사전 계획과 관찰 원자료의 내부 경로·SHA256
```

`protocol`의 세 최소 인원은 각각 1~100의 정수로 사전 선언한다. 3명은 위 계획의 권장안이며 코드가 시장 대표성 기준으로 고정한 값이 아니다. 실제로 완료 관찰이 있는 고유 참여자만 인원에 포함한다. 도시 목록은 `tokyo`, `osaka`, `fukuoka`, 과제 목록은 위 표의 11개 ID다. `devices`는 `mobile`·`desktop` 목록, `target_user`와 초보 여부는 boolean이다. 관찰 `result`는 `completed`·`help_needed`·`not_run`, `data_kind`는 `observed`·`synthetic`·`none`을 쓴다. 실제 가격·근거 과제의 `segment`는 승인된 도시/평면과 같아야 한다.

문제 영향 `severity`는 `blocker`·`help_needed`·`improvement`다. 공개 차단 또는 도움 필요 문제는 해결 설명·증빙 및 원 관찰 종료 뒤 완료한 동일 과제의 재검수 관찰 ID가 필요하다. `open_blocker_count=0`을 썼더라도 미해결 문제나 재검수 누락을 통과시키지 않는다. 이 목록 역시 결과를 채워 넣은 보고서가 아니다. 참가자 수와 완료 여부를 계산하는 근거는 참여자·관찰 행이며 요약 숫자만으로 충분하지 않다.

### 첨부 증빙 형식

`protocol_evidence`, 경보의 `delivery_evidence`·`receipt_evidence`, 관찰의 `evidence`, 문제의 `resolution_evidence`는 각각 `{path, sha256}` 객체다. 경로는 **해당 보고서가 있는 폴더 안**의 상대 경로이고 해시는 실제 파일의 SHA256 소문자 64자리다. 운영 기록을 한 파일에 정리했다면 여러 행이 같은 파일을 참조할 수 있다. 실제 파일 존재·내용 해시·폴더 범위도 검사하므로 보고서에 해시 문자열만 넣어서는 충분하지 않다.

준비 검사 때 파일을 무제한 읽지 않도록 첨부는 개별 256 KiB 이하, 보고서당 고유 경로/해시 32개 이하, 총 읽기 2,000,000바이트 이하로 제한한다. 영상 등 큰 원자료는 별도 비공개 보관소에 두고 이 한도 안의 사실에 근거한 시행 기록·수신 증명·관찰 문서를 첨부한다. 첨부가 사후 변경되면 재검토 전까지 준비 상태가 미충족으로 돌아간다.

## 단계 4. 증거 검토와 공개 결정

운영자는 실제 시행 원자료와 최종 보고서가 일치하는지 확인한다. `acceptance.py`는 비어 있는 검수, 모순된 시각, 단순히 합성 보고서의 scope만 바꾼 기록, 누락·손상된 첨부를 거절한다. SHA256은 문서가 등록 뒤 바뀌었는지 탐지하는 장치이고, 허위 시행 기록을 진짜로 증명하는 서명은 아니다. 자동 준비 검사가 상세 원자료 전체의 진위를 대신 판단하지 않는다.

알림은 7일, 사용성은 30일 이내 보고서를 검토한다. 해당 실행과 코드·공급 설정 fingerprint가 같아야 한다. 운영자가 검토한 뒤에만 `release.json`의 `reports.alerts_drill`, `reports.usability_review`에 설정 폴더 내부 상대 경로와 SHA256을 등록한다. 등록 자체로 공개 권한·실제 공급권·시장 승인·예산 확인을 대체하지 않는다.

인증된 실제 서버의 `/api/v2/readiness`가 모든 항목을 통과하는지 확인한 뒤, 운영자가 접속 범위·가동 시간·장애 대응·비용을 확정하고 공개를 결정한다. 현재는 외부 수신·실제 사용자 보고서가 없으므로 두 항목은 미충족 상태를 유지한다.
