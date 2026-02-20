
import requests
from bs4 import BeautifulSoup
import time
import csv
import re
import statistics
import sys
import random
from pathlib import Path
from datetime import date
import urllib.parse

# --- Configuration ---
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}
DELAY_RANGE = (1.5, 2.5) # Increased slightly

LAYOUT_MAP = {
    "1r": "0",
    "1k": "1",
    "1dk": "2",
    "1ldk": "3"
}

TOKYO_WARDS = {
    "千代田区": "13101", "中央区": "13102", "港区": "13103", "新宿区": "13104", "文京区": "13105",
    "台東区": "13106", "墨田区": "13107", "江東区": "13108", "品川区": "13109", "目黒区": "13110",
    "大田区": "13111", "世田谷区": "13112", "渋谷区": "13113", "中野区": "13114", "杉並区": "13115",
    "豊島区": "13116", "北区": "13117", "荒川区": "13118", "板橋区": "13119", "練馬区": "13120",
    "足立区": "13121", "葛飾区": "13122", "江戸川区": "13123"
}
OSAKA_WARDS = {
    "大阪市都島区": "27102", "大阪市福島区": "27103", "大阪市此花区": "27104", "大阪市西区": "27106",
    "大阪市港区": "27107", "大阪市大正区": "27108", "大阪市天王寺区": "27109", "大阪市浪速区": "27111",
    "大阪市西淀川区": "27113", "大阪市東淀川区": "27114", "大阪市東成区": "27115", "大阪市生野区": "27116",
    "大阪市旭区": "27117", "大阪市城東区": "27118", "大阪市阿倍野区": "27119", "大阪市住吉区": "27120",
    "大阪市東住吉区": "27121", "大阪市西成区": "27122", "大阪市淀川区": "27123", "大阪市鶴見区": "27124",
    "大阪市住之江区": "27125", "大阪市平野区": "27126", "大阪市北区": "27127", "大阪市中央区": "27128"
}

STRUC_TEXT_MAP = {
    "鉄骨鉄筋コンクリート": "src",
    "ＳＲＣ": "src", 
    "鉄筋コンクリート": "rc",
    "ＲＣ": "rc",
    "軽量鉄骨": "light_steel",
    "鉄骨": "steel", 
    "木造": "wood"
}

def get_base_list_url(pref, city_code, layout_param):
    # Only use layout param
    qs = urllib.parse.urlencode({"m": layout_param})
    return f"https://www.chintai.net/{pref}/area/{city_code}/list/?{qs}"

def fetch_page(url):
    time.sleep(random.uniform(*DELAY_RANGE))
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
        else:
            print(f" [WARN] Status {r.status_code} for {url}", file=sys.stderr)
    except Exception as e:
        print(f"[ERR] Fetch failed {url}: {e}", file=sys.stderr)
    return None

def parse_rents_from_html(html, target_structures):
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    
    builds = soup.find_all(class_='cassette_item')
    
    for b in builds:
        try:
            info_table = b.find(class_='bukken_information')
            if not info_table: continue
            
            found_struc = None
            for th in info_table.find_all('th'):
                if "構造" in th.get_text():
                    td = th.find_next_sibling('td')
                    if td:
                        full_text = td.get_text()
                        for jp_s, en_s in STRUC_TEXT_MAP.items():
                            if jp_s in full_text:
                                found_struc = en_s
                                break
                    break
            
            if not found_struc or found_struc not in target_structures:
                continue

            detail_table = b.find(class_='cassette_detail')
            if not detail_table: continue
            
            rows = detail_table.find_all('tr', class_='detail-inner')
            for r in rows:
                price_td = r.find(class_='price')
                if not price_td: continue
                
                num_span = price_td.find(class_='num')
                if not num_span: continue
                
                rent_man_str = num_span.get_text().strip()
                try:
                    rent_yen = int(float(rent_man_str) * 10000)
                    items.append((found_struc, rent_yen))
                except:
                    continue
                    
        except Exception as e:
            continue
            
    return items

def calculate_stats(rents):
    if not rents: return None, 0
    return int(statistics.median(rents)), len(rents)

def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--structures", nargs="+", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    targets = set(args.structures)
    
    municipalities = []
    for m, c in TOKYO_WARDS.items(): municipalities.append(("tokyo", m, c))
    for m, c in OSAKA_WARDS.items(): municipalities.append(("osaka", m, c))
    
    layouts = ["1r", "1k", "1dk", "1ldk"]

    fieldnames = [
        "region_country","prefecture","municipality","layout_type",
        "building_structure","avg_rent_yen","source_name",
        "source_url","source_updated_at","collected_at","method_notes",
        "count"
    ]
    
    with open(args.out, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
        writer.writeheader()

    for pref, muni_name, muni_code in municipalities:
        for ly in layouts:
            m_param = LAYOUT_MAP[ly]
            
            # Pagination Logic
            # We want to collect N>=20 for EACH target structure for this (Muni, Layout)
            # Since we can't filter structure in URL, we fetch "All" (by layout)
            # and accumulate stats for all targets.
            
            print(f"Checking {muni_name} {ly} ...", flush=True)
            
            collected_items = []
            final_url = ""
            
            for page in range(1, 6): # Max 5 pages
                if page == 1:
                    qs = urllib.parse.urlencode({"m": m_param})
                    url = f"https://www.chintai.net/{pref}/area/{muni_code}/list/?{qs}"
                else:
                    qs = urllib.parse.urlencode({"m": m_param})
                    url = f"https://www.chintai.net/{pref}/area/{muni_code}/list/page{page}/?{qs}"
                
                final_url = url
                html = fetch_page(url)
                if not html:
                    break
                
                items = parse_rents_from_html(html, targets)
                if not items:
                    if page == 1:
                        print(f" [INFO] No items on page 1", flush=True)
                    break 
                
                collected_items.extend(items)
                
                # Check if we have enough for all targets
                missing_count = 0
                for st in targets:
                    c = len([x for x in collected_items if x[0] == st])
                    if c < 20: missing_count += 1
                
                if missing_count == 0:
                    break # Done for this layout
            
            # Save results
            rows_to_save = []
            for st in targets:
                rents = [r for s, r in collected_items if s == st]
                median_val, count = calculate_stats(rents)
                
                if count >= 20:
                    row = {
                        "region_country": "JP",
                        "prefecture": pref,
                        "municipality": muni_name,
                        "layout_type": ly.upper(),
                        "building_structure": st,
                        "avg_rent_yen": median_val,
                        "source_name": "CHINTAI",
                        "source_url": final_url,
                        "source_updated_at": date.today().isoformat(),
                        "collected_at": date.today().isoformat(),
                        "method_notes": f"List Median (N={count})",
                        "count": count
                    }
                    rows_to_save.append(row)
                    print(f"   -> {st}: {median_val:,} JPY (N={count})", flush=True)
                else:
                    # Optional: Log insufficient data?
                    # The user prompt requires "missing_report.md" for missing combos.
                    # We will generate that later from CSV.
                    print(f"   -> {st}: Insufficient Data (N={count})", flush=True)

            if rows_to_save:
                with open(args.out, "a", encoding="utf-8", newline="") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction='ignore')
                    writer.writerows(rows_to_save)

if __name__ == "__main__":
    main()
