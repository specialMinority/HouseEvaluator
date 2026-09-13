# 공개 사이트 조회 작업자

무료 운영 구성은 기존 Render HTTPS 웹/API와 운영자 PC의 전용 조회 작업자를 사용한다. 브라우저는 Render API만 호출한다. PC 작업자는 Render로 HTTPS 발신하고 등록된 매물 검색·URL 입력 작업만 처리한다. PC에 수신 포트를 열거나 임의 주소·프로그램을 전달하지 않는다.

이 구성에는 PC 전원·사용자 로그인·인터넷 연결이 필요하다. PC 종료·절전·로그아웃 동안 조회할 수 없으며 화면에서 연결 상태를 확인할 수 있다. 원문 사이트의 정책·접근 가능성은 별개다. 고정 실행 모드에서 원문이 거절하면 해당 작업을 종료하고 다른 출처·호스트로 자동 재실행하지 않는다.

## 설정

Render에는 `HOUSE_EVALUATOR_EXECUTION_MODE=worker`, `HOUSE_EVALUATOR_SEARCH_SOURCE=suumo`, `HOUSE_EVALUATOR_IMPORT_SOURCES=chintai,yahoo_realestate`를 지정한다. `HOUSE_EVALUATOR_WORKER_TOKEN`은 기존 사용자 접속코드와 다른 무작위 비밀값이어야 한다. 설정을 저장하고 배포한다. 사용자 코드로 작업을 가져가거나 작업자 코드로 일반 개인 API를 사용할 수 없다.

PC는 같은 전용 비밀값을 Git에서 제외된 `.runtime/worker-token.txt`에 보관한다. 비밀값은 URL·스크립트·Git·로그에 기록하지 않는다. `scripts/start_personal_worker.ps1`은 Python 경로를 확인하고 고정 Render 원점·출처 설정으로 숨김 작업자를 실행한다. 프로세스 ID와 고정 오류 로그는 `.runtime`에만 남긴다. 조회 조건·결과는 디스크에 저장하지 않는다.

```powershell
./scripts/install_personal_worker_task.ps1
Start-ScheduledTask -TaskName 'HouseEvaluator Public Query Worker'
```

설치 스크립트는 현재 로그인한 사용자 권한만 사용하며 암호를 저장하지 않는다. 다른 실행 경로가 같은 작업 이름을 사용하면 덮어쓰지 않는다. 작업은 다음 로그인 때도 시작된다. 여러 실행을 막고 기본 72시간 실행 제한·배터리 전환 중단을 해제하지만, PC 절전이나 로그아웃을 방지하지 않는다. 컴퓨터의 전체 전원 정책은 바꾸지 않는다.

## 상태와 중단

인증된 `/api/v2/personal/options`는 `execution_mode`, `worker.online`, `search_available`, `import_available`을 반환한다. 마지막 확인 30초 또는 진행 중인 유효 실행권으로 연결을 판정한다. 화면의 연결 재확인은 매물 조회를 실행하지 않는다.

```powershell
Get-ScheduledTask -TaskName 'HouseEvaluator Public Query Worker'
Stop-ScheduledTask -TaskName 'HouseEvaluator Public Query Worker'
```

중단 후 `.runtime/worker.pid`의 프로세스가 종료됐는지도 확인한다. 남아 있으면 실제 실행 경로와 `-m backend.v2.remote_worker` 명령이 일치하는 해당 PID만 종료한다. 자동 시작까지 해제하려면 `Unregister-ScheduledTask -TaskName 'HouseEvaluator Public Query Worker' -Confirm:$false`를 사용한다. 코드 교체는 작업자를 중단하고 새 파일을 반영한 뒤 시작한다.

## 작업 수명과 운영 범위

- 검색·URL 조회는 202로 등록하고 결과를 조회한다. 대기/실행 합계 최대 3개, 동시에 원문을 읽는 작업은 1개다.
- 작업은 100초, 개별 실행권은 75초이며 원문 검색 자체는 기존 45초, URL 입력은 8.5초 제한을 유지한다. 취소·시간 초과 후 같은 원문 작업을 자동 재배달하지 않는다.
- 같은 완료 결과의 업로드 재전송만 허용한다. 작업·결과는 최대 32개, 최대 15분 메모리에만 보관하며 Render 재배포/재시작 시 사라진다.
- PC와 서버의 작은 시각 차이는 서버 UTC와 단조 시계를 기준으로 최대 5초 기다린 뒤 전송한다. 원문의 조회 시각이나 OS 시계를 변경하지 않는다. 차이가 한도를 넘으면 작업 오류로 처리한다.
- 일반 API 본문은 64 KiB, 인증된 작업 결과만 1 MiB까지 허용한다. 결과의 출처·상세 URL·조건·최대 60개 표본과 필드 타입을 다시 검사한다.
- 다른 사용자의 검색 조건과 입력 URL도 운영자의 PC에서 처리한다. 개인별 계정이나 작업자별 권한 분리는 없으며 현재는 접속코드를 공유하는 소규모 미리보기다.
- 작업자의 확인 요청도 Render 수신 트래픽이다. Free의 월 750시간은 워크스페이스에서 공유하며, 계속 연결하면 시간 한도를 소비한다. [Render Free 제한](https://render.com/docs/free)

## 검증 기록

2026-09-13 11:06~11:08 KST에 실제 공개 사이트에서 **URL 조건 자동입력 → 자동검색 → 비교** 흐름을 확인했다. 브라우저는 Render에 접속하고 원문 조회는 설정된 PC 작업자에서 수행했다.

| 확인 항목 | 결과 |
| --- | --- |
| 실행 코드 | `af7231de6c0c3660ea2afecee466d9ea7594c4e1` |
| Render 배포 | `dep-daj09o8ae00c7385ml40`, Live 11:05:55 KST |
| URL 자동입력 | Yahoo! 부동산 상세 링크의 확인된 조건이 실제 입력폼에 반영됨 |
| 자동검색 | SUUMO 목록 3페이지에서 광고 60개 반환, 상세 정보 22개 반영 |
| 비교 화면 | 비교 광고 막대 4개와 대상 막대, 표시 표본 내 순위, 원문 링크 확인 |
| 코드 검증 | 시간 차이 대응 관련 회귀 153개 통과; [GitHub Actions 성공](https://github.com/specialMinority/HouseEvaluator/actions/runs/34732146457) |
| 연결 중단·복구 | 전용 예약 작업과 식별 확인한 작업자만 중단한 뒤 30초 경과 후 화면에서 연결 재확인: 오프라인 안내·검색/URL 버튼 비활성 확인. 작업자 재시작 후 온라인·두 버튼 활성 복구. 입력·기존 그래프 유지, 추가 원문 요청 0회 |

그 이전의 Render 직접 조회는 SUUMO 503, CHINTAI와 Yahoo! 부동산 접근 제한으로 실패했다. PC 작업자 최초 연동에서는 PC와 서버의 작은 시각 차이로 결과가 거절됐고, 조회 시각을 바꾸지 않는 서버 UTC 기준 대기 처리 후 위 흐름을 확인했다. 과거 실패를 성공으로 다시 분류하지 않으며 **Render 단독 직접 조회의 복구 성공은 확인되지 않았다.**

검증 후 작업자는 실행 중 상태로 복구했다. 현재 사용자는 공유 접속 코드로 접근하는 소규모 미리보기다. PC가 켜져 있고 현재 사용자가 로그인한 상태로 인터넷에 연결돼 있어야 하며, 작업자는 현재 사용자 로그인 시 자동 시작하도록 구성한다. 이 설정은 절전·로그아웃·네트워크 단절을 막지 않는다. 원문 사이트의 정책이나 응답 형식이 바뀌면 다시 실패할 수 있다. 이번 확인은 선택 광고의 처리 흐름 검증이며 전체 시장·현재 공실·상시 가용성을 보장하지 않는다.

실제 매물 조건·상세 링크·이름·접속 코드·작업자 비밀값은 이 문서에 기록하지 않는다. 상세 진행은 `DEVELOPMENT_PHASES.md`에 기록한다.
