# Japan WH House Evaluator

> 世界中どこからでもアクセス可能な、日本の賃貸物件評価サービス
>
> AWS/GCP Cloud Run環境にDockerでデプロイされた、外国人向け賃貸評価Webアプリケーションです。

🔗 **Live Demo**: [https://tokyo-wh-api-646481219077.asia-northeast3.run.app/frontend/](https://tokyo-wh-api-646481219077.asia-northeast3.run.app/frontend/)

---

## 🚀 アーキテクチャ (Architecture)

```
Browser (Vanilla JS SPA)
        │ POST /api/evaluate
        ▼
Google Cloud Run (Python / FastAPI-style HTTP)
  ├── backend/src/evaluate.py   ← スコアリングエンジン (JSONLogic)
  ├── spec_bundle_v0.1.2/       ← S1 入力スキーマ / S2 スコアリングスペック
  └── agents/agent_D_benchmark_data/out/
            └── benchmark_rent_raw.json  ← CHINTAIクロールデータ (929行)
```

---

## 📸 スクリーンショット (Screenshots)

### 入力フォーム (Input Form)
<!-- screenshot: 브라우저로 앱 열면 아래 이미지 교체 가능 -->
> [앱 바로가기](https://tokyo-wh-api-646481219077.asia-northeast3.run.app/frontend/) — 입력 폼에서 주요 허브역 / 도도부현 / 월세 등을 입력합니다.

### 평가 결과 (Evaluation Result)
> 위치(Location) · 컨디션(Condition) · 비용(Cost) 3개 축으로 종합 등급(A/B/C/D)이 산출됩니다.

---

## 🔄 CI/CD パイプライン (CI/CD Pipeline)

GitHub → **Cloud Shell** → `gcloud builds submit` → **Cloud Run** 자동 배포

- `main` 브랜치 push → Cloud Shell에서 빌드·배포
- Docker 단일 컨테이너 (Python + 정적 파일 서빙)
- 다운타임 없는 롤링 업데이트 (Cloud Run 관리형)

---

## 📱 主な機能 (Key Features)

### 1. 🏠 종합 물건 평가

| 컴포넌트 | 설명 | 가중치 |
|---------|------|--------|
| **Location** | 허브역 도보시간, 접근성 점수 | 35% |
| **Condition** | 면적·築年·방향·구조·욕실분리 | 25% |
| **Cost** | 월세 벤치마크 비교, 초기비용 배율 | 40% |

### 2. 🗂️ 스펙 기반 스코어링 엔진

- `S1_InputSchema.json` — 입력 필드 정의 (MVP 13개 + 고급 옵션)
- `S2_ScoringSpec.json` — JSONLogic 기반 버킷/리니어/불리언 스코어 룰
- `S3 Report Templates` — 한국어 요약, 리스크 플래그, 교섭 제안

### 3. 📊 CHINTAI 벤치마크 데이터

- **도쿄·사이타마·치바·가나가와·오사카** 5개 권역
- 간마도리(1R/1K/1DK/1LDK) × 건물 구조(木造/RC/SRC 등) 조합
- 929행 실매물 중앙값 데이터 (CHINTAI 스크레이핑)
- `benchmark_confidence`: `high / low / none` 3단계 신뢰도

### 4. ⚠️ 리스크 플래그 & What-If 분석

- 자동 리스크 감지 (HIGH_INITIAL_MULTIPLE, FAR_FROM_STATION, OLD_BUILDING 등)
- What-If 시뮬레이션: 礼金 0엔 / 중개수수료 50% / 초기비용 -5万엔

### 5. 📱 모바일 퍼스트 UI

- 360px 이상 전 해상도 대응
- Mock 모드로 API 없이도 결과 미리보기
- 예시 데이터 5종 (도쿄 신주쿠, 사이타마, 치바, 가나가와, 고급 옵션)

---

## 🛠 技術スタック (Tech Stack)

### Backend
| 항목 | 내용 |
|------|------|
| Language | Python 3.11 |
| HTTP Server | `http.server` (내장) + 커스텀 라우팅 |
| 스코어링 엔진 | JSONLogic 미니멀 구현 (`backend/src/rules/jsonlogic.py`) |
| 벤치마크 | CSV/JSON 파일 기반 + 인덱스 캐시 |

### Frontend
| 항목 | 내용 |
|------|------|
| 프레임워크 | Vanilla JS (ES Modules, 빌드 없음) |
| 스타일링 | Custom CSS (Bootstrap 없음) |
| 스펙 렌더링 | `S1_InputSchema.json` 런타임 로드 → 동적 폼 생성 |

### Infrastructure & DevOps
| 항목 | 내용 |
|------|------|
| Cloud | Google Cloud Run (asia-northeast3 / Seoul) |
| Container | Docker (단일 스테이지) |
| 빌드 | `gcloud builds submit` |
| 데이터 수집 | CHINTAI 스크레이핑 (`scripts/collect_chintai_structure_benchmarks.py`) |

---

## 📁 プロジェクト構成 (Project Structure)

```
tokyo-wh-house-eval/
├── backend/
│   └── src/
│       ├── evaluate.py          # HTTP 서버 + 평가 API 진입점
│       ├── scoring_engine.py    # 컴포넌트별 스코어 계산
│       ├── benchmark_loader.py  # 벤치마크 인덱스 빌드/조회
│       └── rules/
│           └── jsonlogic.py     # JSONLogic 평가기
├── frontend/
│   └── src/
│       ├── main.js              # SPA 진입점 (스펙 기반 폼 렌더)
│       ├── styles.css           # 전체 스타일
│       └── fixtures/            # Mock 모드 예시 데이터
├── spec_bundle_v0.1.2/
│   ├── S1_InputSchema.json      # 입력 스키마 (필드 정의)
│   ├── S2_ScoringSpec.json      # 스코어링 룰 (가중치/버킷)
│   └── C1_ReportTemplates.json  # 리포트 문구 템플릿
├── agents/
│   └── agent_D_benchmark_data/
│       └── out/
│           ├── benchmark_rent_raw.json   # 벤치마크 원본 (929행)
│           └── benchmark_index.json      # 조회용 인덱스
├── scripts/
│   ├── collect_chintai_structure_benchmarks.py  # 데이터 수집
│   ├── generate_reports.py      # 커버리지 리포트 생성
│   └── merge_benchmark_rows.py  # 신규 데이터 병합
├── benchmark_collection/        # 수집 원본 CSV / 보고서
├── Dockerfile
└── README.md
```

---

## 🚀 はじめ方 (Getting Started)

### 1. リポジトリのクローン
```bash
git clone https://github.com/specialMinority/HouseEvaluator.git
cd HouseEvaluator
```

### 2. ローカル実行 (No Docker)
```powershell
# Python 3.11 이상 필요
python -m backend.src.evaluate
# → http://localhost:8000/frontend/ 으로 접속
```

### 3. Mock 모드로 빠른 확인
```
http://localhost:8000/frontend/?mock=1
```
"예시 불러오기" 드롭다운에서 5가지 케이스를 선택해 결과를 확인할 수 있습니다.

### 4. Docker 로컬 실행
```bash
docker build -t wh-eval:local .
docker run --rm -p 8000:8000 wh-eval:local
```

### 5. API 직접 호출
```powershell
$body = @{
  hub_station = "shinjuku"
  prefecture = "tokyo"
  nearest_station_name = "高田馬場"
  station_walk_min = 7
  layout_type = "1K"
  building_structure = "rc"
  area_sqm = 24.5
  building_built_year = 2017
  orientation = "SE"
  bathroom_toilet_separate = $true
  rent_yen = 118000
  mgmt_fee_yen = 8000
  initial_cost_total_yen = 420000
} | ConvertTo-Json

Invoke-RestMethod -Method Post `
  -Uri http://localhost:8000/api/evaluate `
  -ContentType "application/json" `
  -Body $body
```

---

## 📊 벤치마크 데이터 갱신

```powershell
# 누락 데이터 재수집
python scripts/crawl_missing.py

# 리포트 생성 (missing_report.md / summary.md)
python scripts/generate_reports.py

# benchmark_index.json 재빌드
python scripts/merge_benchmark_rows.py --input benchmark_collection/phase2_structure_benchmarks.csv
```

---

## 📜 ライセンス (License)

MIT License

---

> **외국인 워킹홀리데이·유학생·직장인**을 위한 일본 임대 물건 평가 도구입니다.  
> 벤치마크 데이터는 CHINTAI 공개 리스팅 기반으로 수집되었으며, 참고용입니다.
