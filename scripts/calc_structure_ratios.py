import csv
import statistics
from collections import defaultdict

rows = list(csv.DictReader(open('benchmark_collection/phase2_structure_benchmarks.csv', encoding='utf-8')))

# 실제 컬럼명 확인
print("Columns:", list(rows[0].keys()))

# (prefecture, municipality, layout) -> {structure: avg_rent}
combos = defaultdict(dict)
for r in rows:
    key = (r['prefecture'], r['municipality'], r['layout_type'])
    struct = r['building_structure']
    # avg_rent_yen 또는 median_rent_yen 둘 다 시도
    rent_str = r.get('avg_rent_yen') or r.get('median_rent_yen') or r.get('average_rent_yen', '0')
    try:
        rent = int(float(rent_str))
    except:
        continue
    if rent > 0:
        combos[key][struct] = rent


# RC가 있는 조합에서 다른 구조와의 비율 산출
ratios = defaultdict(list)
detail_rows = []
for key, structs in combos.items():
    if 'rc' not in structs:
        continue
    rc_rent = structs['rc']
    for s, rent in structs.items():
        if s == 'rc':
            continue
        ratio = rent / rc_rent
        ratios[s].append(ratio)
        detail_rows.append({
            'pref': key[0], 'muni': key[1], 'layout': key[2],
            'structure': s, 'rc_rent': rc_rent, 'struct_rent': rent,
            'ratio': ratio
        })

print("=" * 65)
print("  구조별 RC 대비 임대료 비율 (Structure / RC Rent Ratio)")
print("=" * 65)
header = f"{'Structure':<15} {'N':>5} {'Mean':>8} {'Median':>8} {'Min':>8} {'Max':>8}"
print(header)
print("-" * 65)

results = {}
for s in ['wood', 'light_steel', 'steel', 'src']:
    if s not in ratios or len(ratios[s]) == 0:
        print(f"{s:<15}  no data")
        continue
    vals = ratios[s]
    avg = sum(vals) / len(vals)
    med = statistics.median(vals)
    mn = min(vals)
    mx = max(vals)
    results[s] = {'n': len(vals), 'mean': avg, 'median': med, 'min': mn, 'max': mx}
    print(f"{s:<15} {len(vals):>5} {avg:>8.4f} {med:>8.4f} {mn:>8.4f} {mx:>8.4f}")

print()
print("=" * 65)
print("  상세 비율 (구 x 간마도리 단위)")
print("=" * 65)

for s in ['wood', 'light_steel', 'steel', 'src']:
    relevant = [d for d in detail_rows if d['structure'] == s]
    if not relevant:
        continue
    print(f"\n--- {s} vs RC ---")
    for d in sorted(relevant, key=lambda x: (x['pref'], x['muni'], x['layout'])):
        print(f"  {d['pref']:>6} {d['muni']:<14} {d['layout']:<5}  "
              f"RC={d['rc_rent']:>7,}  {s}={d['struct_rent']:>7,}  "
              f"ratio={d['ratio']:.4f}")

# 지역별(Tokyo/Osaka) 비율 분리
print()
print("=" * 65)
print("  지역별 구조 비율 (Tokyo vs Osaka)")
print("=" * 65)
for pref in ['Tokyo', 'Osaka']:
    print(f"\n--- {pref} ---")
    for s in ['wood', 'light_steel', 'steel', 'src']:
        vals = [d['ratio'] for d in detail_rows if d['structure'] == s and d['pref'] == pref]
        if not vals:
            print(f"  {s:<15}  no data")
            continue
        avg = sum(vals) / len(vals)
        med = statistics.median(vals)
        print(f"  {s:<15} N={len(vals):>3}  mean={avg:.4f}  median={med:.4f}")

# 간마도리별 비율
print()
print("=" * 65)
print("  간마도리별 구조 비율")
print("=" * 65)
for layout in ['1R', '1K', '1DK', '1LDK']:
    print(f"\n--- {layout} ---")
    for s in ['wood', 'light_steel', 'steel', 'src']:
        vals = [d['ratio'] for d in detail_rows if d['structure'] == s and d['layout'] == layout]
        if not vals:
            print(f"  {s:<15}  no data")
            continue
        avg = sum(vals) / len(vals)
        med = statistics.median(vals)
        print(f"  {s:<15} N={len(vals):>3}  mean={avg:.4f}  median={med:.4f}")
