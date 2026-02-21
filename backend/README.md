# Backend (Rule Engine + Benchmark Matching)

## Run API
Start a local HTTP server that exposes `POST /api/evaluate` and also serves static files from the repo root (so you can open `frontend/` on the same origin):

```powershell
python -m backend.src.server
```

Bind/port overrides (Cloud Run style):

```powershell
$env:HOST="0.0.0.0"
$env:PORT="8080"
python -m backend.src.server
```

Request example:

```powershell
$body = @{
  hub_station = "other"
  hub_station_other_name = "yokohama"
  prefecture = "kanagawa"
  municipality = "横浜市港北区"
  nearest_station_name = "新横浜"
  station_walk_min = 6
  layout_type = "1K"
  area_sqm = 22
  building_built_year = 2018
  orientation = "S"
  bathroom_toilet_separate = $true
  rent_yen = 85000
  mgmt_fee_yen = 5000
  initial_cost_total_yen = 255000
} | ConvertTo-Json -Depth 5

Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8000/api/evaluate -ContentType "application/json" -Body $body
```

## Run tests (Golden + Yokohama)

```powershell
python -m unittest backend.tests.golden_regression
```

## Docker (Cloud Run local smoke)
This repo includes a root `Dockerfile` that runs the same module Cloud Run would.

```powershell
docker build -t tokyo-wh-api:local .
docker run --rm -e PORT=8080 -p 8080:8080 tokyo-wh-api:local
```

Then in another terminal:

```powershell
curl.exe -s -X POST http://127.0.0.1:8080/api/evaluate -H "Content-Type: application/json" --data "{\"hub_station\":\"shinjuku\",\"prefecture\":\"tokyo\",\"nearest_station_name\":\"station\",\"station_walk_min\":8,\"layout_type\":\"1K\",\"area_sqm\":22,\"building_built_year\":2018,\"orientation\":\"S\",\"bathroom_toilet_separate\":true,\"rent_yen\":100000,\"mgmt_fee_yen\":10000,\"initial_cost_total_yen\":300000}"
```

## Spec source of truth
This backend loads specs from:
- `SPEC_BUNDLE_DIR` (if set), else
- `spec_bundle_v0.1.1/` (if present), else
- `spec_bundle_v0.1.0/`

It expects `spec_bundle.json` inside that folder (or individual `S1_InputSchema.json`, `S2_ScoringSpec.json`, `C1_ReportTemplates.json`, `D1_BenchmarkSpec.json`).

## Benchmark data
No crawling. Only parses provided raw files:
- `agents/agent_D_benchmark_data/out/benchmark_rent_raw.json` (preferred) or `.csv`

On first run it builds `backend/data/benchmark_index.json` (and reuses it afterwards).

## Live comparable benchmark (optional)
If enabled (`SUUMO_LIVE=1`, default), `evaluate()` will try a real-time comparable search on a listing portal and (when it finds **3+** comparable listings) use the **median** of `(rent + admin_fee)` as the benchmark.

Provider order is controlled by `LIVE_PROVIDERS` (default: `homes`).
- `LIVE_PROVIDERS=homes` (recommended): uses **LIFULL HOME'S** theme list pages (more stable to fetch/parse without a headless browser).
- `LIVE_PROVIDERS=homes,suumo`: tries HOME'S first, then falls back to SUUMO.

How to tell it was used:
- `derived.benchmark_matched_level`: `homes_live|homes_relaxed|suumo_live|suumo_relaxed`
- `derived.benchmark_n_sources`: number of listings used
- `derived.live_benchmark`: debug info (enabled/attempted/used/search_url/error)

Disable live fetching:

```powershell
$env:SUUMO_LIVE="0"
```

Note: the bundled benchmark dataset is **rent-only** (no management fee / admin fee).  
When the listing has `mgmt_fee_yen > 0` and the benchmark source is rent-only (CSV index fallback), `evaluate()` adds a conservative management-fee estimate (default: `min(input_mgmt_fee_yen, round(benchmark_rent * 0.05))`, capped at 20,000 JPY) before computing `rent_delta_ratio`.

Matching priority (per v0.1.1 prompt):
1) `(prefecture + municipality + layout_type)` exact match → `benchmark_confidence="high"`
2) fallback `(prefecture + layout_type)` → `benchmark_confidence="low"`
3) none → `benchmark_confidence="none"`, benchmark value `null`

## Output shape
`POST /api/evaluate` returns:
- `derived`: `monthly_fixed_cost_yen`, `building_age_years`, `initial_multiple`, `benchmark_monthly_fixed_cost_yen`, `benchmark_mgmt_fee_estimate_yen`, `benchmark_confidence`, `rent_delta_ratio`
- `scoring`: `location_score`, `condition_score`, `cost_score`, `overall_score`
- `grades`: `location_grade`, `condition_grade`, `cost_grade`, `overall_grade`
- `report`: `summary_ko`, `evidence_bullets_ko`, `risk_flags`, `negotiation_suggestions{ko,ja}`, `alternative_search_queries_ja`, `what_if_results`

## Quality checklist
- [x] `prefecture` enum includes `kanagawa` (validated via S1)
- [x] Benchmark match + fallback + confidence implemented
- [x] GoldenInputs + Yokohama(5+) regression passes
- [x] `benchmark_confidence=low/none`에서도 `summary_ko` 생성
- [x] what-if 최소 1개 이상 항상 생성 (S2 `WI_REDUCE_INITIAL_TOTAL_50K`)
