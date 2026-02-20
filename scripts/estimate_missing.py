"""
RC 데이터 기반 누락 구조 임대료 추정 생성기

기존 수집 데이터에서 같은 (구, 간마도리)의 RC 임대료에 구조별 비율을 적용하여
누락된 조합의 임대료를 추정하고 CSV로 출력합니다.
"""
import csv
import statistics
from collections import defaultdict
from datetime import datetime
from pathlib import Path

INPUT_CSV = Path("benchmark_collection/phase2_structure_benchmarks.csv")
MISSING_CSV = Path("benchmark_collection/missing_tasks.csv")
OUTPUT_CSV = Path("benchmark_collection/estimated_benchmarks.csv")

# ── 1. 기존 데이터 로드 ──
rows = list(csv.DictReader(INPUT_CSV.open(encoding="utf-8")))
print(f"기존 데이터: {len(rows)}행")

# (pref, muni, layout) -> {struct: rent}  — pref는 lower() 통일
combos = defaultdict(dict)
for r in rows:
    key = (r["prefecture"].lower(), r["municipality"], r["layout_type"])
    struct = r["building_structure"]
    rent_str = r.get("avg_rent_yen") or r.get("median_rent_yen") or r.get("average_rent_yen", "0")
    try:
        rent = int(float(rent_str))
    except (ValueError, TypeError):
        continue
    if rent > 0:
        combos[key][struct] = rent

# ── 2. 간마도리별 구조 비율 계산 ──
# (layout, struct) -> [ratio, ...]
ratio_by_layout = defaultdict(list)
ratio_all = defaultdict(list)

for key, structs in combos.items():
    if "rc" not in structs:
        continue
    rc_rent = structs["rc"]
    for s, rent in structs.items():
        if s == "rc":
            continue
        ratio = rent / rc_rent
        ratio_by_layout[(key[2], s)].append(ratio)
        ratio_all[s].append(ratio)

# 간마도리별 중앙값 비율 (또는 전체 중앙값 fallback)
def get_ratio(layout: str, struct: str) -> float | None:
    key = (layout, struct)
    if key in ratio_by_layout and len(ratio_by_layout[key]) >= 3:
        return statistics.median(ratio_by_layout[key])
    if struct in ratio_all and len(ratio_all[struct]) >= 3:
        return statistics.median(ratio_all[struct])
    # 하드코딩 fallback
    fallback = {"wood": 0.78, "light_steel": 0.87, "steel": 0.80, "src": 0.94}
    return fallback.get(struct)

print("\n비율 테이블 (간마도리별 median):")
for layout in ["1R", "1K", "1DK", "1LDK"]:
    parts = []
    for s in ["wood", "light_steel", "steel", "src"]:
        r = get_ratio(layout, s)
        if r:
            parts.append(f"{s}={r:.3f}")
    print(f"  {layout}: {', '.join(parts)}")

# ── 3. 누락 조합 로드 ──
missing = list(csv.DictReader(MISSING_CSV.open(encoding="utf-8")))
print(f"\n누락 조합: {len(missing)}건")

# ── 4. 추정 생성 ──
estimated = []
skipped_no_rc = 0
skipped_no_ratio = 0
today = datetime.now().strftime("%Y-%m-%d")

for m in missing:
    pref = m["prefecture"].lower()
    muni = m["municipality"]
    layout = m["layout"]
    struct = m["structure"]

    key = (pref, muni, layout)

    # RC 데이터가 있는지 확인
    rc_rent = combos.get(key, {}).get("rc")
    if not rc_rent:
        skipped_no_rc += 1
        continue

    # 구조 비율
    ratio = get_ratio(layout, struct)
    if not ratio:
        skipped_no_ratio += 1
        continue

    est_rent = int(round(rc_rent * ratio, -2))  # 100엔 단위 반올림

    estimated.append({
        "region_country": "JP",
        "prefecture": pref,
        "municipality": muni,
        "layout_type": layout,
        "building_structure": struct,
        "avg_rent_yen": est_rent,
        "source_name": "estimated_from_rc",
        "source_url": "",
        "source_updated_at": today,
        "collected_at": today,
        "method_notes": f"RC({rc_rent}) x ratio({ratio:.3f})",
        "count": 0,
        "confidence": "estimate",
    })

# ── 5. CSV 출력 ──
fields = [
    "region_country", "prefecture", "municipality", "layout_type",
    "building_structure", "avg_rent_yen", "source_name", "source_url",
    "source_updated_at", "collected_at", "method_notes", "count", "confidence"
]

with OUTPUT_CSV.open("w", encoding="utf-8", newline="") as f:
    w = csv.DictWriter(f, fieldnames=fields, quoting=csv.QUOTE_MINIMAL)
    w.writeheader()
    for row in estimated:
        w.writerow(row)

print(f"\n=== 결과 ===")
print(f"추정 생성: {len(estimated)}건")
print(f"RC 없어서 skip: {skipped_no_rc}건")
print(f"비율 없어서 skip: {skipped_no_ratio}건")
print(f"출력: {OUTPUT_CSV}")

# 구조별 카운트
from collections import Counter
sc = Counter(r["building_structure"] for r in estimated)
print(f"\n구조별 추정 건수:")
for s, c in sc.most_common():
    print(f"  {s}: {c}")

# 최종 커버리지 계산
total_combos = 940
existing = len(rows)
new = len(estimated)
total_covered = existing + new
pct = total_covered / total_combos * 100
print(f"\n커버리지: {existing}(기존) + {new}(추정) = {total_covered}/{total_combos} ({pct:.1f}%)")
