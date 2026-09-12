# 실제 시장 검증 도구와 증거 형식

이 문서는 Phase 6 검증 프로그램의 사용 계약이다. 실제 공급 계약, 실제 검증 표본, 전문가 검수 기록은 아직 제공되지 않았다. 예제와 자동 테스트는 프로그램 동작만 확인한다. 이 파일이나 테스트 통과를 도시별 시장 정확도 검증 완료로 해석하면 안 된다.

## 실행

표준 Python 라이브러리만 필요하다. 포털에 접속하지 않고 명시한 로컬 파일만 읽는다. 공급 원본과 보고서는 공개 정적 파일 경로 밖에 보관한다.

```powershell
python scripts/validate_v2_market.py .runtime/evidence/manifest.json --output .runtime/reports/pilot-001.json
```

JSON과 같은 이름의 Markdown 보고서를 생성한다. 기존 보고서가 있으면 덮어쓰지 않는다. 종료 코드 `0`은 승인 검토 자격이 있는 구간 존재, `3`은 보고서는 생성했으나 증거 기준 미충족, `2`는 입력/파일 오류다. 어느 경우에도 서버 승인 등록부를 변경하지 않는다. 합성 데이터 전용 프로그램 점검은 `--smoke`를 명시한다. 이 경우 실행 성공은 `0`이지만 `eligible_segments=[]`, `eligibility.passed=false`다. 시장 실행에서 합성 자료는 즉시 거절하고, smoke에서 실제 자료를 섞는 것도 거절한다.

라이브러리 인터페이스:

```python
from backend.v2.validation import read_json, validate_market, render_markdown

manifest, manifest_file_hash = read_json(".runtime/evidence/manifest.json")
report = validate_market(manifest, base_dir=".runtime/evidence", smoke=False)
markdown = render_markdown(report)
```

`generated_at`을 포함한 모든 기준 시각을 입력으로 받으므로 같은 파일·정책 코드는 같은 보고서를 만든다. 입력 파일 SHA256과 정렬한 manifest 내용의 SHA256을 기록한다. 파일당 20 MiB, 중복 JSON 키·NaN·Infinity·외부 URL·UNC·상위 디렉터리·심볼릭 링크를 통한 증거 폴더 이탈을 거절한다.

## 증거 묶음

`config/market_validation.example.json`은 경로와 일자를 실제 증거에 맞게 교체할 작성 예시다. 빈 실제 공급 자료나 검증 성공 결과를 만들어주는 파일이 아니다. manifest가 있는 디렉터리가 모든 상대 입력 경로의 기준이다.

```text
evidence/
  manifest.json
  snapshots/source-a-20260901T100000.json
  snapshots/source-a-20260902T100000.json
  ... 계약이 허용한 각 시점의 완전 스냅숏
  holdouts.json
  human_reviews.json
  supply_history.json
```

manifest 필드:

| 필드 | 의미 |
|---|---|
| `schema_version` | `2.0` |
| `validation_id` | 사전 확정한 검증 프로토콜 식별자 |
| `generated_at` | 보고서의 명시적 기준 시각, 타임존 포함 |
| `protocol_frozen_at` | 요청셋·정책·기준을 동결했다고 운영자가 기록한 시각 |
| `development_cutoff` | 개발·정책 선택 구간 종료 |
| `holdout_start`, `holdout_end` | 최종 보류 검증 구간 |
| `snapshots` | `{path, available_at}` 배열. 파일의 `as_of`와 실제 내부 가용 시각은 서로 다를 수 있음 |
| `holdouts_path` | 평가 대상 모집가격 참조 파일 |
| `human_reviews_path` | 선택 입력. 빠지면 전문가 관련 기준 모두 미충족 |
| `supply_history_path` | 선택 입력. 빠지면 30일 공급·반영 지연 기준 미충족 |

시각은 `protocol_frozen_at <= development_cutoff < holdout_start <= holdout_end <= generated_at` 순서를 지킨다. 공급 데이터의 저장·비교·표시 권리가 보고서 생성 시각에도 유효해야 한다. 과거 파일의 보유 권한이 종료됐으면 연장/재허가 없이 재검증에 사용하지 않는다.

프로그램은 로컬 기록의 진위를 외부에서 인증하지 못한다. 동결 시각과 가용 시각은 운영자가 제공한 증거다. 실운영에서는 공급 수신 로그, 변경 불가한 실행 이력, 검토자와 권리 증빙을 대조해야 한다. 사후에 시각을 바꿔 검증 구간을 통과시키지 않는다.

## 보류 요청과 누수 방지

`holdouts.json`:

```json
{
  "requests": [
    {
      "request_id": "heldout-0001",
      "evaluated_at": "2026-09-13T11:00:00Z",
      "reference_snapshot_id": "supplier-snapshot-0001",
      "reference_observation_id": "supplier-observation-0001"
    }
  ]
}
```

대상 가격과 특성은 직접 기입한 정답 대신 보관된 관측을 참조한다. 참조 대상은 평가 시각에 이용 가능했던 활성 매물이어야 하고 관리비가 알려져 있어야 한다. 모집 상태 확인은 24시간 이내여야 한다. 같은 canonical 호실을 여러 관측·다른 요청 ID로 반복해서 표본을 부풀릴 수 없다.

각 평가에서는 해당 시각 이전에 실제 이용 가능했던 공급원별 최신 **완전 스냅숏** 하나만 고른다. 나중에 도착한 과거 날짜 파일도 과거 평가에 사용할 수 없다. 스냅숏의 `as_of`가 주장한 `available_at` 이후면 거절한다. 동일 공급원·동일 `as_of`의 충돌 파일도 거절한다.

전체 보류 요청에서 등장한 **모든 대상 건물**을 모든 요청의 비교 자료에서 제거한다. 자기 호실만 제외하는 방법보다 보수적이다. 다른 보류 요청의 대상 건물도 비교 자료가 되지 않는다. 무작위 행 분할은 사용하지 않는다. 건물/역/구조/계약 유형/평가 날짜 분포와 사용한 스냅숏 ID, 건물 제외 수를 보고한다.

이 도구는 모델을 학습하지 않는다. 이미 고정된 비교 정책을 시간·건물 분리 요청셋에서 검증한다. 내부적으로만 후보 정책이 승인됐다고 가정하여 높은/낮은 판정 규칙을 시험하고 `hypothetical_judgment`에 기록한다. 서비스 승인에는 영향을 주지 않는다. 요청 모집가격을 후보 선택이나 임계값 조정에 사용하지 않는다.

## 지표의 정확한 의미

- 모집가격 정답 대용값은 `rent_yen + mgmt_fee_yen`이다. 실제 계약가격이나 적정 가격이 아니다.
- 비교 가능률 분모는 사전 고정한 모든 유효 보류 요청이다. 비교 품질 조건을 통과한 요청만 분자다. 부족·오래됨·제한 결과는 모두 보류율에 포함한다.
- 오차는 품질 기준을 통과한 요청의 `abs(비교 중앙값 - 모집가격) / 모집가격`이다. 중앙값(MdAPE)과 P90을 보고한다. 품질 기준 미달 요청은 오차를 0으로 채우지 않는다.
- 기준선은 같은 평가 시각·동일 도시/시구/평면/주택 유형/계약 유형의 활성 호실 단순 중앙값이다. 동일 호실의 충돌 가격은 빼고 중복 광고는 한 번만 센다. 검증 대상 건물은 기준선에서도 제거한다.
- 기준선 개선은 **동일한 평가 가능 요청쌍**에서 `(기준선 MdAPE - 비교 MdAPE) / 기준선 MdAPE`다. 기준선 오차가 0이거나 측정 불가능하면 개선 증거로 인정하지 않는다.
- 판정 안정성은 공급원 하나 제거 및 비교 건물 하나 제거를 각각 실행하는 결정적 jackknife다. 높은↔낮은 전환율뿐 아니라 교란 후 보류 수를 함께 보고한다. 통계적 신뢰구간이나 무작위 bootstrap 결과라고 표현하지 않는다.
- 정책 바이트 SHA256을 기록한다. 정책 코드가 바뀌면 예전 보고서로 자동 승인하지 않도록 런타임이 검사한다.

## 전문가 검수

`human_reviews.json`은 `reviews`, `unit_audits` 두 배열을 갖는다. 아래 ID는 형식 예시일 뿐 실제 관측을 의미하지 않는다.

```json
{
  "reviews": [
    {
      "request_id": "heldout-0001",
      "reviewer_id": "reviewer-a",
      "reviewed_at": "2026-09-13T12:00:00Z",
      "expected_judgment": "unknown",
      "candidates": [
        {"unit_id": "canonical-unit-0002", "acceptable": null}
      ]
    }
  ],
  "unit_audits": [
    {
      "snapshot_id": "supplier-snapshot-0001",
      "observation_id": "supplier-observation-0001",
      "reviewer_id": "reviewer-a",
      "reviewed_at": "2026-09-13T12:00:00Z",
      "core_fields_correct": null,
      "status_correct": null,
      "price_unit_error": null,
      "residual_duplicate": null,
      "false_merge": null
    }
  ]
}
```

검토자가 본 비교 후보 전체를 정확히 한 번씩 라벨링해야 한다. 쉬운 후보만 선택해 제출하면 거절한다. `acceptable`은 true/false/null이고 null은 적합 비율 분모에 남는다. 같은 요청/검토자 중복은 거절한다. 여러 검토자의 기대 판정 불일치는 따로 집계한다. 블라인드 검수 절차의 실제 이행은 운영자가 확인한다.

기대 판정은 `higher`, `similar`, `lower`, `unknown`이다. 기대 판정을 알고 있는 비교 가능 요청에서 모든 판정 불일치율과 반대 방향 오판율을 각각 측정한다. unknown을 정답으로 치지 않고 기대 판정 확인 비율을 별도로 제한한다. 최소 표본은 검토 횟수가 아닌 고유 요청 수다.

호실 감사는 보관 관측을 참조하며 canonical 호실당 한 번만 인정한다. 정답률에서 null은 정답이 아니고, 오류 없음 확인에서 null은 오류 없음이 아니다. 미확인 숫자도 보고한다. 1건의 검토나 같은 호실 반복으로 200개 감사 표본을 채울 수 없다.

## 30일 공급 기록

```json
{
  "events": [
    {
      "source_id": "licensed-source-a",
      "snapshot_id": "supplier-snapshot-0001",
      "status": "success",
      "observed_at": "2026-09-13T10:04:00Z"
    },
    {
      "source_id": "licensed-source-a",
      "status": "error",
      "observed_at": "2026-09-13T10:05:00Z"
    }
  ]
}
```

성공 이벤트는 실제 보관된 동일 공급원 스냅숏을 참조해야 한다. 성공 시각은 내부 적용 완료 시각이다. 지연은 완료 시각에서 manifest의 `available_at`을 뺀 값이다. 하나의 스냅숏을 여러 성공 이벤트로 제출할 수 없다. 최종 보류 평가 날짜까지 이어지는 연속 30개 달력 날짜에 **공급원마다** 성공 기록이 있어야 한다. 수신 지연이나 실패 기록은 따로 보고한다. 이것은 전달된 사건 기록의 검사이며 전체 업타임을 외부에서 독립 측정한 결과가 아니다.

## 승인 검토 자격 초안

제품 기획서의 수락 목표를 도시×평면별로 적용한다. 아래 최소 표본 일부는 기획서의 도시 단위 제안보다 보수적인 **개발 제안값**이다. 실제 수집 결과를 보고 낮춰서 통과시키지 않는다. 수정은 별도 사전 프로토콜 버전과 새로운 보류 평가로 진행한다.

| 기준 | 제안 문턱 |
|---|---|
| 보류 요청 | 구간별 100개 고유 호실, 30개 대상 건물, 7개 평가 날짜 이상 |
| 비교 가능률 | 70% 이상 |
| 모집가격 MdAPE / 기준선 개선 | 10% 이내 / 15% 이상 상대 개선 |
| 비교 적합성 | 100개 고유 요청 검수, 후보 적합 90% 이상 |
| 판정 검수 | 기대 판정 확인 요청 30개 이상, 확인 비율 90% 이상 |
| 판정 불일치 / 높은↔낮은 오판 | 각각 5% 이하 |
| 원자료 감사 | 200개 고유 호실, 핵심 필드 98%, 모집 상태 95% 이상 |
| 가격/단위 오류 | 0건, 미확인도 오류 없음으로 인정하지 않음 |
| 잔여 중복 / 잘못 병합 | 각각 2% 이하 |
| 안정성 | 품질 기준 유지 교란 30개 이상, 높은↔낮은 전환 5% 이하 |
| 공급 | 공급원별 연속 30일 증거, 반영 지연 P95 900초 이하 |

역권·건물군·계약 유형 분포를 사람도 검토해야 한다. 표본이 없는 역권/구조/계약 유형까지 정확도가 입증됐다는 뜻이 아니다. 도시×평면 승인 범위와 실제 공급 공개 범위를 운영자가 일치시켜야 한다. API 부하, 장애/삭제 훈련, 공급 계약 원문, 예산과 운영 책임자는 Phase 7 및 별도 승인 자료다.

## 보고서와 승인 등록부 연결

보고서 형식은 `schema_version="market-validation-1.0"`이다. 최상위에 `report_kind`, `generated_at`, `policy_version`, `policy_sha256`, `source_ids`, `input_hashes`, `split`, `supply`, `segments`, `eligible_segments`, `eligibility`, `requests`, `limitations`를 둔다.

런타임 승인 검토 시 JSON 파일 자체의 SHA256, 현재 비교 코드 SHA256, 정책 버전과 공급원 ID 전체 일치, `report_kind="market"`, 보고 시각, `eligibility.passed=true`, 승인 요청 구간의 `eligible_for_approval=true`를 확인한다. 해당 구간의 모든 개별 `gates`가 true인지도 확인해야 한다. 합성 보고서나 빈 파일·임의 텍스트는 승인 증거가 아니다. 운영자가 별도 만료 시각과 승인자를 기입하더라도 공급 권리와 현재성 검사는 계속 적용한다.

현재 실제 자료가 없는 상태에서 예상되는 정상 결과는 **증거 미충족과 승인 보류**다. 테스트용 관측을 observed로 표기한 단위 테스트가 있어도 해당 파일은 외부 공개용 시장 증거로 사용하지 않는다.
