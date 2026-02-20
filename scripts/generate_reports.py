
import csv
import itertools
from collections import defaultdict
from datetime import date

# Configuration
INPUT_CSV = "benchmark_collection/phase2_structure_benchmarks.csv"
MISSING_REPORT_MD = "benchmark_collection/missing_report.md"
SUMMARY_MD = "benchmark_collection/summary.md"

TOKYO_WARDS = [
    "千代田区", "中央区", "港区", "新宿区", "文京区",
    "台東区", "墨田区", "江東区", "品川区", "目黒区",
    "大田区", "世田谷区", "渋谷区", "中野区", "杉並区",
    "豊島区", "北区", "荒川区", "板橋区", "練馬区",
    "足立区", "葛飾区", "江戸川区"
]
OSAKA_WARDS = [
    "大阪市都島区", "大阪市福島区", "大阪市此花区", "大阪市西区",
    "大阪市港区", "大阪市大正区", "大阪市天王寺区", "大阪市浪速区",
    "大阪市西淀川区", "大阪市東淀川区", "大阪市東成区", "大阪市生野区",
    "大阪市旭区", "大阪市城東区", "大阪市阿倍野区", "大阪市住吉区",
    "大阪市東住吉区", "大阪市西成区", "大阪市淀川区", "大阪市鶴見区",
    "大阪市住之江区", "大阪市平野区", "大阪市北区", "大阪市中央区"
]

LAYOUTS = ["1R", "1K", "1DK", "1LDK"]
STRUCTURES = ["wood", "light_steel", "steel", "rc", "src"]

def main():
    # 1. Load Data
    data = defaultdict(dict) # (pref, muni) -> { (layout, struc): row }
    
    try:
        with open(INPUT_CSV, "r", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
            for r in rows:
                key = (r["layout_type"], r["building_structure"])
                # standardized pref/muni check?
                # The collection script outputs "tokyo" and "千代田区"
                loc = (r["prefecture"], r["municipality"])
                data[loc][key] = r
    except FileNotFoundError:
        print(f"File not found: {INPUT_CSV}")
        return

    # 2. Generate Missing Report
    missing_lines = []
    missing_count = 0
    total_expected = 0
    
    # Check Tokyo
    for ward in TOKYO_WARDS:
        loc = ("tokyo", ward)
        for ly, st in itertools.product(LAYOUTS, STRUCTURES):
            total_expected += 1
            if (ly, st) not in data[loc]:
                missing_lines.append(f"| Tokyo | {ward} | {ly} | {st} | No data (N<20 or 0) |")
                missing_count += 1

    # Check Osaka
    for ward in OSAKA_WARDS:
        loc = ("osaka", ward)
        for ly, st in itertools.product(LAYOUTS, STRUCTURES):
            total_expected += 1
            if (ly, st) not in data[loc]:
                missing_lines.append(f"| Osaka | {ward} | {ly} | {st} | No data (N<20 or 0) |")
                missing_count += 1
                
    with open(MISSING_REPORT_MD, "w", encoding="utf-8") as f:
        f.write("# Missing Data Report\n\n")
        f.write(f"Generated at: {date.today()}\n")
        f.write(f"Missing: {missing_count} / {total_expected} combinations\n\n")
        f.write("| Prefecture | Municipality | Layout | Structure | Reason |\n")
        f.write("|---|---|---|---|---|\n")
        f.write("\n".join(missing_lines))
        
    print(f"Generated {MISSING_REPORT_MD}")

    # 3. Verify Specific Checks (Edogawa 1LDK Wood vs RC)
    print("\n--- Verification: Edogawa 1LDK Wood vs RC ---")
    edogawa_wood = data[("tokyo", "江戸川区")].get(("1LDK", "wood"))
    edogawa_rc = data[("tokyo", "江戸川区")].get(("1LDK", "rc"))
    
    if edogawa_wood and edogawa_rc:
        wood_rent = int(edogawa_wood["avg_rent_yen"])
        rc_rent = int(edogawa_rc["avg_rent_yen"])
        print(f"Edogawa 1LDK Wood: {wood_rent:,} JPY")
        print(f"Edogawa 1LDK RC:   {rc_rent:,} JPY")
        if wood_rent < rc_rent:
            print("[PASS] Wood rent is lower than RC rent.")
        else:
            print("[WARN] Wood rent is NOT lower than RC rent.")
    else:
        print("[INFO] Edogawa 1LDK Wood or RC data missing. Cannot verify yet.")

    # 4. Generate Summary
    with open(SUMMARY_MD, "w", encoding="utf-8") as f:
        f.write("# Data Collection Summary\n\n")
        f.write(f"**Date:** {date.today()}\n")
        f.write(f"**Total Collected Rows:** {len(rows)}\n")
        f.write(f"**Missing Combinations:** {missing_count}\n")
        f.write("\n## Breakdown by Structure\n")
        
        struc_counts = defaultdict(int)
        for r in rows:
            struc_counts[r["building_structure"]] += 1
            
        for st in STRUCTURES:
            f.write(f"- **{st}:** {struc_counts[st]}\n")

    print(f"Generated {SUMMARY_MD}")

if __name__ == "__main__":
    main()
