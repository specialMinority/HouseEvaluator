
from bs4 import BeautifulSoup
import sys

STRUC_TEXT_MAP = {
    "木造": "wood",
    "軽量鉄骨": "light_steel",
    "鉄骨": "steel", 
    "鉄筋コンクリート": "rc",
    "鉄骨鉄筋コンクリート": "src",
    "ＳＲＣ": "src", 
    "ＲＣ": "rc"
}

def parse_rents_from_html(html, target_structures):
    soup = BeautifulSoup(html, 'html.parser')
    items = []
    
    builds = soup.find_all(class_='cassette_item')
    print(f"Found {len(builds)} building blocks")
    
    for i, b in enumerate(builds):
        try:
            # 1. Structure
            info_table = b.find(class_='bukken_information')
            if not info_table:
                print(f"Block {i}: No info table")
                continue
            
            found_struc = None
            found_struc_text = "None"
            for th in info_table.find_all('th'):
                if "構造" in th.get_text():
                    td = th.find_next_sibling('td')
                    if td:
                        full_text = td.get_text().strip()
                        found_struc_text = full_text
                        for jp_s, en_s in STRUC_TEXT_MAP.items():
                            if jp_s in full_text:
                                found_struc = en_s
                                break
                    break
            
            print(f"Block {i}: Structure='{found_struc_text}' -> {found_struc}")
            
            if not found_struc or found_struc not in target_structures:
                continue

            # 2. Rents
            detail_table = b.find(class_='cassette_detail')
            if not detail_table: continue
            
            rows = detail_table.find_all('tr', class_='detail-inner')
            print(f"  -> Found {len(rows)} rooms")
            
            for r in rows:
                price_td = r.find(class_='price')
                if not price_td: continue
                
                num_span = price_td.find(class_='num')
                if not num_span: continue
                
                rent_man_str = num_span.get_text().strip()
                try:
                    rent_yen = int(float(rent_man_str) * 10000)
                    items.append((found_struc, rent_yen))
                    print(f"    -> Rent: {rent_yen}")
                except:
                    continue
                    
        except Exception as e:
            print(f"Error in block {i}: {e}")
            continue
            
    return items

with open("debug_live_1r.html", "r", encoding="utf-8") as f:
    html = f.read()

print("--- Testing Wood/RC ---")
items = parse_rents_from_html(html, {"wood", "rc", "steel", "src", "light_steel"})
print(f"Total items parsed: {len(items)}")
