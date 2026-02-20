
import requests
from bs4 import BeautifulSoup
import time
import statistics
import sys
import random
import urllib.parse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
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

def fetch_page(url):
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"[ERR] Fetch failed {url}: {e}")
    return None

def parse_rents(html, targets):
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    
    builds = soup.find_all(class_='cassette_item')
    for b in builds:
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
        
        if not found_struc or found_struc not in targets:
            continue

        detail_table = b.find(class_='cassette_detail')
        if not detail_table: continue
        
        rows = detail_table.find_all('tr', class_='detail-inner')
        for r in rows:
            price_td = r.find(class_='price')
            if not price_td: continue
            num_span = price_td.find(class_='num')
            if not num_span: continue
            try:
                rent_yen = int(float(num_span.get_text().strip()) * 10000)
                items.append((found_struc, rent_yen))
            except: pass
    return items

def main():
    # Edogawa (13123), 1LDK (3)
    url_base = "https://www.chintai.net/tokyo/area/13123/list/?m=3"
    print(f"Checking Edogawa 1LDK: {url_base}")
    
    all_items = []
    for page in range(1, 6):
        if page == 1: url = url_base
        else: url = f"https://www.chintai.net/tokyo/area/13123/list/page{page}/?m=3"
        
        print(f"Page {page}...")
        html = fetch_page(url)
        if not html: break
        
        items = parse_rents(html, {"wood", "rc"})
        if not items: break
        all_items.extend(items)
        time.sleep(1)

    wood_rents = [r for s, r in all_items if s == "wood"]
    rc_rents = [r for s, r in all_items if s == "rc"]
    
    print(f"Wood: N={len(wood_rents)}")
    if wood_rents:
        print(f"  Median: {int(statistics.median(wood_rents)):,} JPY")
    
    print(f"RC: N={len(rc_rents)}")
    if rc_rents:
        print(f"  Median: {int(statistics.median(rc_rents)):,} JPY")

    if wood_rents and rc_rents:
        wood_med = statistics.median(wood_rents)
        rc_med = statistics.median(rc_rents)
        if wood_med < rc_med:
            print("[PASS] Wood rent is lower than RC rent.")
        else:
            print("[WARN] Wood rent is NOT lower than RC rent.")

if __name__ == "__main__":
    main()
