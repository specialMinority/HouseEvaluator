# building_structure 벤치마크(Phase2/3) 실데이터 수집/반영

이 프로젝트는 `building_structure`(건물 구조)가 입력되면 **구조별 벤치마크가 있으면 우선 사용**하고, 없으면 기존 `all`(구조 미구분) 데이터로 폴백합니다.

로컬 Windows 환경은 외부 네트워크가 막혀 있을 수 있으므로, **실데이터 수집은 Cloud Shell에서 실행**하는 것을 권장합니다.

---

## Phase2 (MVP): `wood`, `rc`

### 1) LIFULL 구조별 평균임대료 수집 (자동)

LIFULL HOME'S의 `list` 페이지에 표시되는 **`平均賃料` 값**을 읽어서 CSV를 생성합니다.

**Bash (Cloud Shell)**

```bash
python scripts/collect_lifull_structure_benchmarks.py \
  --prefectures tokyo osaka \
  --osaka-city-only \
  --structures wood rc \
  --layouts 1r 1k 1dk 1ldk \
  --out benchmark_collection/phase2_lifull_wood_rc.csv
```

### 2) 수집 결과를 벤치마크 raw/index에 반영

```bash
python scripts/merge_benchmark_rows.py --input benchmark_collection/phase2_lifull_wood_rc.csv
```

---

## Phase3: `light_steel`, `steel`, `src` 확장

```bash
python scripts/collect_lifull_structure_benchmarks.py \
  --prefectures tokyo osaka \
  --osaka-city-only \
  --structures light_steel steel src \
  --layouts 1r 1k 1dk 1ldk \
  --out benchmark_collection/phase3_lifull_extra_structures.csv

python scripts/merge_benchmark_rows.py --input benchmark_collection/phase3_lifull_extra_structures.csv
```

---

## 검증

```bash
python -m unittest discover -s backend/tests -p "*.py"
```

정상 반영되면 `backend/data/benchmark_index.json`의 `by_pref_muni_layout_structure`가 빈 딕셔너리가 아니게 됩니다.

---

## 배포 ZIP 갱신 (이전 ZIP 보존)

**PowerShell (로컬)**

```powershell
$ts = Get-Date -Format "yyyyMMdd_HHmmss"
Copy-Item wh-cloudbuild.zip "wh-cloudbuild_${ts}_bak.zip"
python scripts/make_cloudbuild_zip.py wh-cloudbuild.zip
```

**Bash (Cloud Shell)**

```bash
ts="$(date +%Y%m%d_%H%M%S)"
cp -f wh-cloudbuild.zip "wh-cloudbuild_${ts}_bak.zip"
python scripts/make_cloudbuild_zip.py wh-cloudbuild.zip
```

