import csv
from collections import defaultdict

rows = list(csv.DictReader(open('benchmark_collection/phase2_structure_benchmarks.csv', encoding='utf-8')))
combos = defaultdict(dict)
for r in rows:
    key = (r['prefecture'], r['municipality'], r['layout_type'])
    struct = r['building_structure']
    rent_str = r.get('avg_rent_yen') or '0'
    try:
        rent = int(float(rent_str))
    except:
        continue
    if rent > 0:
        combos[key][struct] = rent

rc_keys = [k for k, v in combos.items() if 'rc' in v]
print("RC combos:", len(rc_keys))
for k in rc_keys[:5]:
    print("  key:", k, "-> RC:", combos[k]['rc'], "structs:", list(combos[k].keys()))

missing = list(csv.DictReader(open('benchmark_collection/missing_tasks.csv', encoding='utf-8')))
print("\nmissing_tasks first 5:")
for m in missing[:5]:
    key = (m['prefecture'], m['municipality'], m['layout'])
    rc_found = 'rc' in combos.get(key, {})
    print("  key:", key, "struct:", m['structure'], "RC found:", rc_found)

pref_in_data = set(r['prefecture'] for r in rows)
pref_in_missing = set(m['prefecture'] for m in missing)
print("\ndata prefs:", pref_in_data)
print("missing prefs:", pref_in_missing)

# 누락 조합 중 RC가 있는 건 몇 개?
count_rc_match = 0
for m in missing:
    key = (m['prefecture'], m['municipality'], m['layout'])
    if 'rc' in combos.get(key, {}):
        count_rc_match += 1
print("\nmissing with RC match:", count_rc_match, "/", len(missing))
