# Phase 7.3 배포 검수와 선택적 TLS

현재는 기존 PC 한 대에서 로컬 운영을 검수한다. 실제 공급 계약·시장 검증·사용성 검수·공개 도메인이 없으므로 외부 제한 공개는 아직 시행하지 않았다. 이 문서의 공개 구성은 도메인과 공개 준비가 갖춰진 뒤 사용하는 예제이며, 작성만으로 서버·도메인·인증서·외부 접속을 생성하지 않는다.

초기 설정과 수집·권리·보관 절차는 [무료 우선 실행 안내](FREE_FIRST_DEPLOYMENT.md), 실제 공급 검증은 [시장 검증 안내](MARKET_VALIDATION_V2.md), 현재 완료 여부는 [단계별 진행표](../DEVELOPMENT_PHASES.md)를 따른다. 설정 예제의 빈 공급 목록과 미승인 상태를 실제 증거처럼 바꾸지 않는다.

## 실행 환경 선택

| 구성 | 이 프로젝트에서의 용도 | 남은 조건 |
| --- | --- | --- |
| 기존 PC + Python/SQLite | 추가 서버 구독 없이 개발·운영 훈련 | PC 가동 시간, 전기·회선, 별도 장비 백업 |
| 기존 PC + Docker + Caddy | 도메인 확보 후 초대한 이용자에게 HTTPS 제공 | DNS·공인 접근·포트·공급권·검증·운영자 승인 |
| Render 무료 웹 서비스 | 현재 SQLite 운영 구성에는 부적합 | 유휴 15분 후 중지, 재시작·재배포 시 로컬 자료 유실, 영구 디스크 미지원 |

Render의 무료 PostgreSQL도 생성 후 30일 만료된다. 현재 앱은 PostgreSQL을 지원하지 않으므로 서비스 주소만 교체해 운영할 수 없다. 무료 서비스 조건은 변경될 수 있으며 위 내용은 2026-09-13 확인 기준이다. [Render 공식 무료 서비스 문서](https://render.com/docs/free)

기존 PC를 선택해도 데이터 이용료·도메인·전기·통신까지 무료로 보장되지는 않는다. 실제 공급 비용과 월 구독비 0엔 조건, 초대 범위, 운영 담당자와 가동 시간을 결정한 뒤 공개 승인에 기록한다. 이 작업에서 클라우드 계정 생성이나 유료 자원 구매는 하지 않았다.

## 로컬 검수와 공개 상태 구분

| 검사 | 인증 | 성공 의미 |
| --- | --- | --- |
| `GET /healthz` | 없음, 저장소 조회 없음 | HTTP 프로세스가 응답함 |
| `GET /api/v2/health` | 설정된 접속 코드 필요 | 저장소와 이용 가능한 자료 요약을 조회함 |
| `GET /api/v2/readiness` | 설정된 접속 코드 필요 | 200이면 공개 기준 통과, 503이면 항목별 미충족 |
| 컨테이너 `healthy` | 내부 `/healthz` 사용 | 시장 정확도·공급권·사용자 검수를 뜻하지 않음 |

빈 공급 설정의 `readiness=503`은 정상적인 차단이다. 공개 모드는 실제 검증이 끝나지 않은 평가를 503으로 차단한다. 실제 자료 훈련은 먼저 loopback 개발 구성에서 수행한다. 합성 자료 훈련은 `synthetic_smoke`로 남으며 실제 공개 증거를 대신하지 않는다.

한 호스트·한 API 프로세스·로컬 SQLite 볼륨이 지원 범위다. 컨테이너의 메모리·CPU 제한은 초기 설정이며 실제 공급량에 대한 성능 보장이 아니다. API는 TCP 상대 IP를 기준으로 요청을 제한하므로 프록시 뒤 초대자들이 분당 한도를 공유한다. 현재 예제의 기본값은 전체 120회/분이며 사용자별 한도가 아니다. 공개 전 실제 사용자 수로 측정하고 필요한 경우 명시적으로 조정한다.

## 도메인 확보 뒤 사용하는 공개 예제

[독립 Compose 예제](../deploy/compose.public.example.yaml)와 [Caddy 설정](../deploy/Caddyfile.example)을 준비했다. 기존 `compose.yaml`에 겹치는 override가 아니므로 두 파일을 함께 `-f`로 전달하지 않는다. 예제는 API의 호스트 포트를 열지 않고 Caddy의 TCP 80/443만 연다. 접속 코드·앱 이미지·도메인 환경변수가 없으면 설정 해석이 실패한다. `PILOT=1`, `DEMO=0`, `LEGACY=0`은 공개 구성에서 고정한다.

Caddy는 도메인 DNS가 서버를 가리키고 외부 80/443 접근과 영구 쓰기 가능한 데이터 경로가 갖춰지면 인증서 발급·갱신과 HTTPS 리디렉션을 처리한다. 이 예제의 `caddy-data`는 인증서와 개인키를 보존하며, `caddy-config`는 Caddy 설정 상태를 보존한다. `localhost` 인증서는 초대자의 브라우저에서 신뢰되는 공개 인증서가 아니다. 공유기·방화벽·회선 또는 공인 접근 제한이 있으면 현재 PC에 이 방식을 바로 적용할 수 없다. [Caddy 자동 HTTPS](https://caddyserver.com/docs/automatic-https), [Caddy 프록시 안내](https://caddyserver.com/docs/quick-starts/reverse-proxy)

실행 전에 확인할 사항:

1. 실제 공급 계약과 권한·검증 보고서를 준비하고, 공개할 도시×평면만 승인한다.
2. 현재 이미지·공급 설정으로 복구·부하·알림·[한국인 사용성 검수](PILOT_USABILITY_V2.md)를 완료한다.
3. 도메인·TLS·공개 대상·가동 시간·복구 담당자·월 비용을 결정한다.
4. 사용한 앱 이미지와 Caddy 이미지를 검수한 고정 버전 또는 digest로 기록한다. 예제 기본 `caddy:2` 태그는 재현 가능한 버전 고정을 대신하지 않는다.
5. 별도 공개 프로젝트 볼륨에 허용된 실제 자료를 반입하고 검증한다. 아래 `houseevaluator-pilot`은 로컬 Compose와 다른 프로젝트이므로 기존 자료가 자동 이동하지 않는다. 새 운영 경로 복구는 [저장 수명주기 절차](STORAGE_LIFECYCLE_V2.md)를 따른다.
6. `readiness`의 모든 기준을 확인하고 운영자 승인 후 외부 포트를 연다.

아래는 위 조건이 충족된 뒤 저장소 루트에서 사용하는 명령이다. 현재 단계에서는 외부 공개 명령을 실행하지 않았다. 환경변수 `HOUSE_EVALUATOR_IMAGE`, `HOUSE_EVALUATOR_CADDY_IMAGE`, `HOUSE_EVALUATOR_DOMAIN`, `HOUSE_EVALUATOR_ACCESS_TOKEN`에는 실제 검수값을 사용한다. 접속 코드를 출력·URL·커밋에 남기지 않는다.

```powershell
# 설정 검사만 수행하며 컨테이너를 시작하거나 포트를 열지 않는다.
docker compose -p houseevaluator-pilot -f deploy/compose.public.example.yaml config --quiet

# 공급권·운영 증거·도메인·공개 승인 완료 후에만 실행한다.
docker compose -p houseevaluator-pilot -f deploy/compose.public.example.yaml --profile ingestion up -d

# 공개 접속과 작업을 중단한다. 데이터 볼륨은 보존한다.
docker compose -p houseevaluator-pilot -f deploy/compose.public.example.yaml down
```

`--profile ingestion`은 수집 작업을 함께 시작한다. 공급 키는 설정의 `auth_env` 이름에 맞춰 해당 변수만 비공개 Compose override에서 수집 서비스로 전달한다. 호스트의 환경변수가 모두 컨테이너에 자동 전달되지는 않는다. 공개 명령의 Compose 파일 경로 기준은 `deploy/`이며, 기본 설정 마운트 `../.runtime/config`는 저장소의 `.runtime/config`다. `HOUSE_EVALUATOR_CONFIG_DIR`를 사용한다면 컨테이너에서 읽을 파일이 들어 있는 호스트 절대 경로를 지정하는 편이 명확하다.

## 검수 증거와 로그

공급 설정은 `/config`에 읽기 전용으로 마운트한다. 훈련 보고서를 컨테이너에서 생성할 때에는 `/data/reports` 등 쓰기 가능한 경로를 사용한다. 이후 `docker compose ... cp api:/data/reports/보고서.json .runtime/config/reports/보고서.json`처럼 호스트로 복사하고 내용·SHA256을 확인한 뒤 승인 설정에 상대 경로를 등록한다. `/config` 안에서 직접 새 보고서를 쓰는 명령은 실패한다. 파일명은 기존 증거를 덮어쓰지 않도록 매번 새로 정한다.

운영 증거의 fingerprint는 앱 코드와 공급 설정 바이트를 포함한다. Windows 절대 경로를 컨테이너 경로로 바꾸거나 코드를 변경하면 훈련을 최종 실행 설정에서 다시 수행한다. 같은 코드·같은 상대 경로 설정이면 실행 루트가 다르다는 이유만으로 fingerprint가 달라지지는 않는다.

Caddy 접근 로그는 요청 전체와 응답 헤더를 제거하고 상태·크기·지연을 남긴다. API와 수집 작업, Caddy의 Docker 로그는 서비스별 파일당 10 MB·최대 3개로 제한한다. 이는 용량 상한이며 정해진 일수 보관을 보장하지 않는다. 오류 로그도 운영 자료로 취급하고 공개하지 않는다. 외부 Caddy 경로의 `/healthz`는 404로 닫으며 시장 상태 API는 계속 접속 코드로 보호한다. [Caddy 로그 필터](https://caddyserver.com/docs/caddyfile/directives/log), [Docker 로그 순환](https://docs.docker.com/engine/logging/drivers/json-file/)

공개 중 공급권 만료·모집 확인 시각 초과·운영 증거 만료가 발생하면 평가가 보류 또는 차단될 수 있다. 담당자는 인증된 준비 상태와 장애 기록을 확인하고 공급·증거를 갱신한다. 컴퓨터 전원·회선 장애와 외부 백업 복구는 컨테이너 재시작 정책만으로 해결되지 않으므로 실제 운영 훈련에서 별도로 확인한다.
