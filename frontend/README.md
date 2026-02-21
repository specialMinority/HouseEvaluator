# Frontend (Spec-driven MVP)

## Single source of truth
This frontend **does not invent keys/enums** and renders the report in the R0 order.  
It loads S1 from the repo snapshot at runtime.

- Spec snapshot (preferred): `spec_bundle_v0.1.1/` (if present)
- Fallbacks in this repo: `spec_bundle_v0.1.0/`, `integrate/merged/`

## Run (no build, no dependencies)
Recommended: run the backend server, which also serves static files from the repo root (same-origin API).

```powershell
cd C:\Users\PC\Downloads\tokyo-wh-house-eval
python -m backend.src.server
```

Open:
- Mock mode: `http://localhost:8000/frontend/?mock=1`
- Real API (same origin): `http://localhost:8000/frontend/`

Alternative (UI-only): if you only want to view the frontend in mock mode, you can run a static server:
```powershell
python -m http.server 8000
```

## API wiring
- Endpoint: `POST /api/evaluate`
- Request body: **V0 input keys only** (the form is generated from `S1_InputSchema.json`)
- Response: rendered **as-is** using V0 `report/scoring/grades` keys

Optional query params:
- `apiBase=http://localhost:3000` (if you run backend separately; CORS may apply)
- `specBase=../spec_bundle_v0.1.1` (force spec snapshot base)

## Mock usage
- Turn on **Mock 모드** toggle, or use `?mock=1`
- Use the dropdown **예시 불러오기…**

Fixtures:
- `frontend/src/fixtures/mock_inputs.json`
- `frontend/src/fixtures/mock_response.json`

## Benchmark dataset (read-only display)
The result page includes a **벤치마크 근거** card.  
It uses response fields if present (`source_name`, `source_updated_at`, `method_notes`), otherwise it summarizes the raw dataset:
- `agents/agent_D_benchmark_data/out/benchmark_rent_raw.json` (preferred)
- `agents/agent_D_benchmark_data/out/benchmark_rent_raw.csv` (fallback)

## Quality checklist
- Basic inputs show only `S1.mvp_required_fields` (10~12)
- `ui.advanced=true` fields are only under Advanced toggle
- `prefecture` allows only `tokyo|saitama|chiba|kanagawa`
- Yokohama flow: `prefecture=kanagawa` + `municipality/nearest_station_name`
- `benchmark_confidence=low/none` does not break the UI
- Usable at **360px width** (mobile-first)
