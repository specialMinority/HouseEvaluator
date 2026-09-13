# Render 무료 제한 공개 배포

작성 기준: 2026-09-13. [render.yaml](../render.yaml)은 재현용 Blueprint 설정이며, 아래 1~5절은 구성·운영 절차다. **첫 실제 서비스는 공개 Git 저장소를 연결해 수동 생성했다.** Blueprint는 사용하지 않았으며, 실제 배포와 검증 결과는 6절에 구분해 기록한다.

## 1. 배포할 구성

개인 비교 화면·API를 기존 [Dockerfile](../Dockerfile)로 한 서비스에 배포한다. 공급사 계약이나 유료 저장소를 추가하지 않는다. 명시된 가격을 선택 표본끼리 비교하며, 전체 시장 시세를 보장하지 않는다.

| 설정 | 값과 의미 |
| --- | --- |
| 서비스 | `houseevaluator-personal`, Web Service, Docker |
| 실행 | Dockerfile의 `python -m backend.src.server`; 별도 `startCommand` 불필요 |
| 규모 | `plan: free`, `region: singapore`, `numInstances: 1` |
| 업데이트 | `autoDeployTrigger: "off"`; **Blueprint Auto Sync도 별도로 끈다** |
| 공개 포트 | `HOST=0.0.0.0`, `PORT=8080` |
| 상태 검사 | `/healthz` |
| 개인 비교 / 공개 검색 | `HOUSE_EVALUATOR_PERSONAL=1`, `HOUSE_EVALUATOR_PUBLIC_SEARCH=1` |
| 자동검색 출처 | `HOUSE_EVALUATOR_SEARCH_SOURCE=yahoo_realestate` (미지정 시 기존 `suumo`) |
| URL 자동입력 출처 | `HOUSE_EVALUATOR_IMPORT_SOURCES=yahoo_realestate`; 실제 클라우드에서 접근 가능한 출처만 활성화 |
| 시연 / 구버전 / 공급형 파일럿 | `HOUSE_EVALUATOR_DEMO=0`, `HOUSE_EVALUATOR_LEGACY=0`, `HOUSE_EVALUATOR_PILOT=0` |
| 구버전 실시간 수집 | `SUUMO_LIVE=0`; 개인 검색은 별도 `HOUSE_EVALUATOR_PUBLIC_SEARCH`로 제어 |
| 임시 SQLite | `HOUSE_EVALUATOR_DB=/tmp/houseevaluator/v2.sqlite3` |
| API 요청 제한 | `HOUSE_EVALUATOR_REQUESTS_PER_MINUTE=120` |
| 접속 코드 | `HOUSE_EVALUATOR_ACCESS_TOKEN`, Render에서 `generateValue: true`로 생성 |

Docker의 `CMD`를 사용하므로 `dockerCommand`는 생략한다. `free`, `singapore`, 단일 인스턴스, 자동 배포 끄기, 생성형 환경변수는 [공식 Blueprint 명세](https://render.com/docs/blueprint-spec)에 따른다. 무료 디스크·Postgres·Redis·추가 worker·예약 작업을 생성하지 않는다.

Render는 `PORT` 환경변수 변경을 지원한다. 외부 HTTPS는 Render가 처리하고 내부 HTTP 8080으로 전달하므로 Caddy와 인증서 파일이 필요 없다. 제공되는 `onrender.com` 주소를 사용한다. [포트와 HTTPS 전달 방식](https://render.com/docs/web-services#port-binding)

서버의 `Runtime`은 SQLite 부모 디렉터리를 자동 생성한다. Docker의 비관리자 사용자도 `/tmp` 아래에 새 디렉터리를 만들 수 있다. Dockerfile에 있는 `/data`, `/backups`의 `VOLUME` 선언은 Render 영구 저장소를 제공하지 않으며 이 구성의 DB 경로로 사용하지 않는다.

## 2. 무료 운영의 범위

- 요청이 15분 없으면 서비스가 쉬고, 다음 접속 때 다시 시작하는 데 약 1분이 걸릴 수 있다.
- 재배포·재시작·휴면 때 로컬 파일과 SQLite 변경이 사라진다. 무료 서비스에는 영구 디스크를 붙일 수 없다.
- 워크스페이스의 월 750시간은 다른 무료 서비스와 공유한다. 대역폭·빌드 사용량도 확인해야 한다. 결제수단이 연결된 계정은 초과 사용이 비용으로 이어질 수 있어 `plan: free`만으로 청구 상한 0원을 보장하지 않는다.
- 외부 인터넷으로 많은 요청을 발생시키면 Render가 서비스를 정지할 수 있다. 자동검색을 대량 수집·주기 수집 용도로 사용하지 않는다.

이는 무료 미리보기·소수 이용자 검증 구성이다. 상시 운영이나 가용성을 보장하지 않는다. [Render 무료 서비스 제한](https://render.com/docs/free)

앱 자체의 검색 작업도 메모리에만 있고 기본 유효기간은 15분이다. 작업은 최대 2개가 동시에 실행되며, 재시작하면 작업 ID가 유효하지 않게 된다. 이미 화면에 받은 목록은 해당 탭 메모리에 남아 비교할 수 있지만 새로고침하면 입력·목록·접속 코드가 지워진다. 저장·복구 기능이 있는 서비스로 안내하지 않는다.

## 3. 최초 배포 순서

이번 배포는 공개 Git 저장소 연결을 사용한다. **New → Web Service → Public Git Repository**에서 `https://github.com/specialMinority/HouseEvaluator`를 연결하고 브랜치를 `codex/houseevaluator-v2`로 지정한다. Docker, Singapore, Free를 선택하고 위 환경변수를 입력한다. 수동 생성 화면에서는 `generateValue`를 환경변수 값으로 입력하지 않고 별도로 생성한 무작위 접속 코드를 비밀 환경변수로 설정한다. 기존 `main`은 이전 구현이므로 배포 대상으로 선택하지 않는다. 공개 Git URL 연결은 GitHub 계정 전체에 대한 추가 권한이 필요하지 않으며 수동으로 배포한다.

다음은 같은 설정을 Blueprint로 재현할 때의 절차다.

1. 배포할 GitHub 브랜치에 이 파일, `render.yaml`, 현재 Dockerfile과 개인 비교 코드가 함께 있는지 확인한다. 매니페스트의 저장소·브랜치는 위 배포 대상을 명시한다.
2. Render 대시보드에서 **New → Blueprint**를 선택하고 사용할 GitHub 저장소를 연결한다. Blueprint Path는 루트의 `render.yaml`이다.
3. 적용 예정 목록에서 **무료 Web Service 1개**, Singapore, 디스크·DB 없음, 접속 코드 자동 생성을 확인한다. 기존 이름의 서비스와 연결된다는 안내가 나오면 의도한 대상인지 확인하고, 새 서비스가 목적이면 매니페스트 이름을 구분한다.
4. 선택한 소스·계정·무료 구성을 검토한 후 **Deploy Blueprint**로 최초 배포를 실행한다. 이 버튼은 외부 서비스를 실제 생성한다. [Blueprint 생성 절차](https://render.com/docs/infrastructure-as-code#setup)
5. Blueprint의 **Settings → Auto Sync를 No**로 바꾼다. 서비스의 Auto-Deploy도 Off인지 확인한다. `autoDeployTrigger: "off"`만으로는 Blueprint 설정 변경의 자동 적용까지 차단하지 않는다. 이후 구성 변경은 Manual Sync로 반영한다. [자동 Sync 끄기](https://render.com/docs/infrastructure-as-code#disabling-automatic-sync)
6. 서비스의 Environment에서 생성된 `HOUSE_EVALUATOR_ACCESS_TOKEN`을 확인한다. 코드나 문서에 복사하지 말고 허용된 이용자에게만 별도로 전달한다. 서비스 URL의 접속 코드 입력란에 넣어 연결한다.
7. 다음 절차로 실제 Render 환경을 검증하고 배포 기록을 작성한다.

`generateValue: true`는 현재 공식 명세상 **256비트 무작위 값의 Base64 표현**을 만든다. 32자 hex를 가정하면 안 된다. 환경변수가 이미 있으면 새로 생성하지 않는다. 앱은 공백 없는 ASCII 32~256자를 허용하므로 생성 형식과 호환된다. 이 코드는 모든 허용 이용자가 공유하는 API 접속 코드이며, 개별 사용자 계정·권한 분리 기능은 아니다. HTML과 `/healthz`는 공개되고 `/api/` 요청에 코드가 필요하다. [무작위 비밀값 생성](https://render.com/docs/blueprint-spec#generating-random-secrets)

## 4. 최초 배포 확인

| 확인 | 통과 기준 |
| --- | --- |
| 실행 로그 | Python 시작 성공, DB 권한 오류나 포트 바인딩 오류 없음 |
| 공개 상태 검사 | 실제 서비스의 `/healthz`가 HTTP 200, `{"status":"ok"}` 반환 |
| 기본 화면 | `/`가 개인 비교 화면으로 연결되고 접속 코드 입력란이 보임 |
| 인증 차단 | 코드 없이 `/api/v2/personal/options`를 열면 401 |
| 정상 코드 | 코드 입력 후 도시·입력폼 사용 가능, 옵션 `enabled=true`, `search_enabled=true` |
| 직접 비교 | 직접 확인한 매물의 조건·가격·URL을 입력하고 결과·차이·원문 링크를 확인 |
| 자동검색 1회 | 실제 역·면적 입력으로 검색하여 작업 등록 → 진행 → 결과 또는 명확한 실패 안내까지 확인 |
| 검색 실패 대응 | 차단·변경·시간 초과여도 직접 입력 비교를 계속할 수 있음 |
| 좁은 화면 | 휴대전화에서 가격 그래프·매물 조건·원문·비교 버튼 접근 가능 |
| 코드 폐기 | 로그아웃 후 API 재사용이 막히고 다시 접속 코드를 요청함 |

`/healthz`는 공급 데이터나 검색 가능성을 검사하지 않는 프로세스 생존 검사다. Render HTTP 상태 검사의 성공 기준은 5초 안의 2xx 또는 3xx 응답이다. 개인 비교 배포에서는 공급형 승인에 의존하는 `/api/v2/readiness`를 상태 검사로 지정하지 않는다. [Render 상태 검사](https://render.com/docs/health-checks)

로컬에서 SUUMO 검색이 성공했다는 사실만으로 Render IP에서도 성공했다고 기록하지 않는다. 실제 배포의 성공·차단 여부, 수집 범위, 페이지 수와 시각을 확인한다. 차단 우회나 반복 재시도 대신 아래의 자동검색 중단 절차를 사용한다.

## 5. 요청 제한과 장애 처리

현재 요청 제한은 API에만 적용되고 서버가 직접 보는 접속 IP를 기준으로 한다. `X-Forwarded-For`는 신뢰하지 않는다. Render 프록시를 거치면 여러 이용자가 같은 한도를 공유할 수 있으므로 **120회/분은 이용자마다 보장되는 처리량이 아니다.** 검색 진행 조회도 이 한도를 사용한다. 429가 나오면 1분 기다리고 동시 이용을 줄인다. 원인을 확인하지 않고 상한을 크게 올리지 않는다.

인증에 실패한 요청은 정상 코드 요청과 별도 한도를 사용한다. 검색 기록이 32개에 도달하면 오래된 완료·실패·취소 기록부터 비우므로 완료 기록 때문에 새 검색이 15분간 막히지 않는다. 진행 중인 검색의 실행 슬롯은 이 과정에서 해제하지 않는다.

| 상황 | 대응 |
| --- | --- |
| 처음 접속이 느림 | 무료 휴면 복귀 시간을 기다린 후 `/healthz` 확인 |
| 인증 401 | URL이 아니라 접속 코드 입력란 사용, 환경변수 앞뒤 공백 여부 확인 |
| `rate_limited` / `search_busy` | 기다린 뒤 재시도하거나 직접 입력 사용 |
| 자동검색만 장애 | `HOUSE_EVALUATOR_PUBLIC_SEARCH=0`으로 바꾸고 새 환경변수가 적용되는 배포 실행 |
| 변경 코드 장애 | 이전에 검증한 커밋으로 수동 배포 후 위 확인 반복 |
| 접속 코드 유출 | 새 무작위 코드로 환경변수를 교체하고 배포; 이전 코드 401 확인 |

자동검색을 중단해도 `HOUSE_EVALUATOR_PERSONAL=1`을 유지하면 직접 입력 비교가 남는다. Blueprint 관리 값은 다음 Sync 때 덮어써질 수 있으므로 긴급 대시보드 변경을 했다면 `render.yaml`의 값도 맞춘다. [Blueprint 외부 변경의 처리](https://render.com/docs/infrastructure-as-code#modifying-a-resource-outside-of-its-blueprint)

코드 업데이트는 검증한 커밋을 **Manual Deploy → Deploy a specific commit**으로 지정한다. 환경변수 변경을 적용할 때 **Restart service만 누르지 않는다**. Render의 Restart service는 현재 실행 중인 버전의 환경변수를 재사용한다. Environment에서 새 값을 저장하고 해당 값을 반영하는 배포를 실행한다. [수동 배포와 재시작 차이](https://render.com/docs/deploys#manual-deploys)

## 6. 배포 기록

2026-09-13 KST 기준. 계정 식별정보와 접속 코드는 기록하지 않는다. 아래 비교 검증에는 명시적인 가상 매물만 사용했으며 실제 시장가격 검증을 뜻하지 않는다.

| 항목 | 실제 기록 |
| --- | --- |
| 서비스 URL | [houseevaluator-personal.onrender.com](https://houseevaluator-personal.onrender.com) |
| 서비스 ID | `srv-daiu1ntg1s2s738ojah0` |
| 배포 방식 | 공개 Git 저장소 연결 후 Web Service 수동 생성; Blueprint 미사용 |
| 배포 브랜치 / 커밋 SHA | `codex/houseevaluator-v2` / `fe884643227ab675e8628bfe060cca2c5bd5a2dc` |
| Render 배포 ID | `dep-daiu1otg1s2s738ojcv0` |
| 배포 시각 | 시작 08:31:47 → 서버 시작 08:32:16 → Live 08:32:23 KST; 대시보드 소요 35.1초 |
| 인스턴스 / 비용 구성 | Singapore, Free, 0.1 CPU·512 MB, Web Service 1개; 유료 리소스 추가 없음; 생성 과정에서 결제카드 입력 요구 없음 |
| 자동 배포 | Auto-Deploy Off 확인; Blueprint Auto Sync는 미사용으로 해당 없음 |
| HTTPS / health / 인증 | 외부 HTTPS 접속, `/healthz` 200, 코드 없는 API 401, 정상 코드의 개인 옵션 200 확인 |
| 화면·API 비교 | 개인 HTML·JS·CSS 정상; 가상 3매물의 총액 95,000·100,000·103,000엔과 대상 105,000엔, 표시한 4개 중 낮은 가격순 4번째 및 원문 URL 보존 확인 |
| 데스크톱 브라우저 직접 입력 | 가상 1매물 95,000엔 / 대상 100,000엔 → 2개 중 2번째·5,000엔 차이; 원문 링크·상단 및 고정 비교 버튼 확인 |
| 모바일 브라우저 | 실제 HTTPS 390×844에서 23/23 통과: 가상 95,000엔/대상 100,000엔 그래프·순위 2/2·차이 5,000엔·원문·고정 버튼·로그아웃 초기화 정상, 가로 넘침·브라우저 오류 없음. 자동검색 요청 0회 |
| 비허용 경로 | 일반 비공개 경로는 404. 경로 탐색 입력 3개 중 2개는 404, `/%2e%2e/etc/passwd`는 400으로 거절됨 |
| 실제 자동검색 1회 | 공개 테스트 조건 新宿区·新宿·1K·25m²로 작업 등록 202 및 조회 200 확인. **소스 보고서 `http_503`, 수집 목록 0개로 자동검색 성공은 확인하지 못함** |
| 운영 전환 | 서비스 중단·접속 코드 교체·자동검색만 끄기는 절차만 준비; 실제 전환 미실행 |

원격 HTTP 검사는 08:34:00~08:34:08 KST에 한 번 실행했다. 처음 스크립트는 비허용 경로를 모두 404로 요구하여 경로 탐색 요청의 400 거절 때문에 실패로 기록했다. 이후 **기존 응답만 오프라인 재평가**하여 일반 비공개 경로는 계속 404를 요구하고, 경로 탐색 문법은 400 또는 404를 허용하도록 수정했다. 정상 응답·리다이렉트·인증 요구·서버 오류를 거절 성공으로 취급하지 않는다. 재평가에서 외부 요청을 추가하지 않았으며 원본 보고서는 바꾸지 않았다.

재평가 결론은 `degraded_public_search`다. 기본 HTTP·인증·가상 비교 검사는 통과했지만 실제 자동검색은 503·0건 상태다. 이 응답만으로 사이트 장애인지 호스팅 환경의 제한인지 구분할 수 없다. 400 응답이 어느 계층에서 발생했는지도 상태 코드만으로 확정하지 않는다.

세부 증거는 로컬 `.runtime`에만 보관하며 GitHub에 게시하지 않는다.

- 원본: `render-remote-smoke-report.json`
- 변경하지 않은 보존본: `render-remote-smoke-report-original-dc6ab20317204f13.json`
- 원본 SHA-256: `dc6ab20317204f13f35f5572aae8bfd4ac5346e0555a6170da00a976e37c8ca2`
- 오프라인 재평가: `render-remote-smoke-reassessment.json` (`network_requests_sent=0`, 새로운 배포 검증 실행 아님)

로컬 준비 검증에서는 YAML 파싱, 무료 구성·환경변수 타입·중복 키, 생성형 Base64 접속 코드 형식의 `AccessPolicy` 수용, 새 SQLite 부모 디렉터리 자동 생성이 통과했다. 필드는 [공식 JSON Schema](https://render.com/schema/render.yaml.json)와 명세를 대조했다. Render CLI/API의 전체 Blueprint 검증은 실행하지 않았으며, 이번 실제 배포는 위의 수동 서비스 생성 방식으로 검증했다. 이용자 검수나 클라우드 자동검색의 성공으로 확대 해석하지 않는다.
