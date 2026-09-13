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
- 일반 API 본문은 64 KiB, 인증된 작업 결과만 1 MiB까지 허용한다. 결과의 출처·상세 URL·조건·최대 60개 표본과 필드 타입을 다시 검사한다.
- 다른 사용자의 검색 조건과 입력 URL도 운영자의 PC에서 처리한다. 개인별 계정이나 작업자별 권한 분리는 없으며 현재는 접속코드를 공유하는 소규모 미리보기다.
- 작업자의 확인 요청도 Render 수신 트래픽이다. Free의 월 750시간은 워크스페이스에서 공유하며, 계속 연결하면 시간 한도를 소비한다. [Render Free 제한](https://render.com/docs/free)

## 검증 기록

단위·통합·브라우저 검사를 준비했다. 실제 배포 사이트의 URL 입력 → 자동검색 → 비교 및 연결 중단/복구 확인이 끝나기 전까지 운영 완료로 표시하지 않는다. 상세 진행은 `DEVELOPMENT_PHASES.md`에 기록한다.
