"""
crawl_missing.py
missing_report.md 를 파싱하여 아직 수집되지 않은 조합을 재크롤링하고
기존 CSV에 추가 저장한다.
최대 10페이지까지 시도하여 N>=20 을 목표로 한다.
"""

import requests
from bs4 import BeautifulSoup
import time, csv, re, statistics, sys, random
from datetime import date
import urllib.parse

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}
DELAY_RANGE = (1.5, 2.5)

LAYOUT_MAP = {"1R": "0", "1K": "1", "1DK": "2", "1LDK": "3"}

WARD_CODE = {
    # Tokyo 23구
    "千代田区": ("tokyo","13101"), "中央区": ("tokyo","13102"), "港区": ("tokyo","13103"),
    "新宿区": ("tokyo","13104"), "文京区": ("tokyo","13105"), "台東区": ("tokyo","13106"),
    "墨田区": ("tokyo","13107"), "江東区": ("tokyo","13108"), "品川区": ("tokyo","13109"),
    "目黒区": ("tokyo","13110"), "大田区": ("tokyo","13111"), "世田谷区": ("tokyo","13112"),
    "渋谷区": ("tokyo","13113"), "中野区": ("tokyo","13114"), "杉並区": ("tokyo","13115"),
    "豊島区": ("tokyo","13116"), "北区": ("tokyo","13117"), "荒川区": ("tokyo","13118"),
    "板橋区": ("tokyo","13119"), "練馬区": ("tokyo","13120"), "足立区": ("tokyo","13121"),
    "葛飾区": ("tokyo","13122"), "江戸川区": ("tokyo","13123"),
    # Osaka 24구
    "大阪市都島区": ("osaka","27102"), "大阪市福島区": ("osaka","27103"),
    "大阪市此花区": ("osaka","27104"), "大阪市西区": ("osaka","27106"),
    "大阪市港区": ("osaka","27107"), "大阪市大正区": ("osaka","27108"),
    "大阪市天王寺区": ("osaka","27109"), "大阪市浪速区": ("osaka","27111"),
    "大阪市西淀川区": ("osaka","27113"), "大阪市東淀川区": ("osaka","27114"),
    "大阪市東成区": ("osaka","27115"), "大阪市生野区": ("osaka","27116"),
    "大阪市旭区": ("osaka","27117"), "大阪市城東区": ("osaka","27118"),
    "大阪市阿倍野区": ("osaka","27119"), "大阪市住吉区": ("osaka","27120"),
    "大阪市東住吉区": ("osaka","27121"), "大阪市西成区": ("osaka","27122"),
    "大阪市淀川区": ("osaka","27123"), "大阪市鶴見区": ("osaka","27124"),
    "大阪市住之江区": ("osaka","27125"), "大阪市平野区": ("osaka","27126"),
    "大阪市北区": ("osaka","27127"), "大阪市中央区": ("osaka","27128"),
}

STRUC_TEXT_MAP = {
    "鉄骨鉄筋コンクリート": "src",
    "ＳＲＣ": "src",
    "鉄筋コンクリート": "rc",
    "ＲＣ": "rc",
    "軽量鉄骨": "light_steel",
    "鉄骨": "steel",
    "木造": "wood",
}

OUT_CSV = "benchmark_collection/phase2_structure_benchmarks.csv"
MISSING_MD = "benchmark_collection/missing_report.md"

FIELDNAMES = [
    "region_country","prefecture","municipality","layout_type",
    "building_structure","avg_rent_yen","source_name",
    "source_url","source_updated_at","collected_at","method_notes","count"
]


def parse_missing(path):
    """missing_report.md의 테이블 행을 파싱해 (muni, layout, struct) 집합 반환"""
    missing = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            m = re.match(r'\|\s*(?:Tokyo|Osaka)\s*\|\s*(.+?)\s*\|\s*(\w+)\s*\|\s*(\w+)\s*\|', line)
            if m:
                muni, layout, struct = m.group(1).strip(), m.group(2).strip(), m.group(3).strip()
                missing.append((muni, layout, struct))
    return missing


def fetch_page(url):
    time.sleep(random.uniform(*DELAY_RANGE))
    try:
        r = requests.get(url, headers=HEADERS, timeout=30)
        if r.status_code == 200:
            return r.text
    except Exception as e:
        print(f"  [ERR] {url}: {e}", flush=True)
    return None


def parse_rents(html, targets):
    soup = BeautifulSoup(html, "html.parser")
    items = []
    for b in soup.find_all(class_="cassette_item"):
        try:
            info = b.find(class_="bukken_information")
            if not info: continue
            struc = None
            for th in info.find_all("th"):
                if "構造" in th.get_text():
                    td = th.find_next_sibling("td")
                    if td:
                        txt = td.get_text()
                        for jp, en in STRUC_TEXT_MAP.items():
                            if jp in txt:
                                struc = en
                                break
                    break
            if not struc or struc not in targets:
                continue
            det = b.find(class_="cassette_detail")
            if not det: continue
            for row in det.find_all("tr", class_="detail-inner"):
                num = row.find(class_="num")
                if not num: continue
                try:
                    items.append((struc, int(float(num.get_text().strip()) * 10000)))
                except: pass
        except: pass
    return items


def main():
    missing = parse_missing(MISSING_MD)
    print(f"재시도 대상: {len(missing)}건", flush=True)

    # 지역별·레이아웃별로 그룹화 → 한 번 페이지를 가져올 때 여러 구조 타입을 동시에 체크
    from collections import defaultdict
    groups = defaultdict(set)  # (muni, layout) -> {struct, ...}
    for muni, layout, struct in missing:
        groups[(muni, layout)].add(struct)

    print(f"고유 (구, 간取り) 그룹: {len(groups)}건", flush=True)

    # 기존 CSV에 추가
    with open(OUT_CSV, "a", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDNAMES, extrasaction="ignore")
        # 헤더는 이미 있으므로 쓰지 않음

        for (muni, layout), structs in groups.items():
            if muni not in WARD_CODE:
                print(f"  [SKIP] 알 수 없는 지역: {muni}", flush=True)
                continue
            pref, code = WARD_CODE[muni]
            m_param = LAYOUT_MAP.get(layout)
            if not m_param:
                continue

            print(f"Checking {muni} {layout} {structs} ...", flush=True)
            collected = []

            for page in range(1, 11):  # 최대 10페이지
                if page == 1:
                    qs = urllib.parse.urlencode({"m": m_param})
                    url = f"https://www.chintai.net/{pref}/area/{code}/list/?{qs}"
                else:
                    qs = urllib.parse.urlencode({"m": m_param})
                    url = f"https://www.chintai.net/{pref}/area/{code}/list/page{page}/?{qs}"

                html = fetch_page(url)
                if not html:
                    break
                items = parse_rents(html, structs)
                if not items and page > 1:
                    break  # 더 이상 결과 없음
                collected.extend(items)

                # 모든 타깃이 20개 달성했으면 종료
                if all(
                    len([r for s, r in collected if s == st]) >= 20
                    for st in structs
                ):
                    break

            final_url = url
            rows_saved = 0
            for st in structs:
                rents = [r for s, r in collected if s == st]
                if len(rents) >= 20:
                    med = int(statistics.median(rents))
                    writer.writerow({
                        "region_country": "JP",
                        "prefecture": pref,
                        "municipality": muni,
                        "layout_type": layout,
                        "building_structure": st,
                        "avg_rent_yen": med,
                        "source_name": "CHINTAI",
                        "source_url": final_url,
                        "source_updated_at": date.today().isoformat(),
                        "collected_at": date.today().isoformat(),
                        "method_notes": f"List Median (N={len(rents)}) retry",
                        "count": len(rents),
                    })
                    f.flush()
                    print(f"   -> {st}: {med:,} JPY (N={len(rents)})", flush=True)
                    rows_saved += 1
                else:
                    print(f"   -> {st}: 데이터 부족 (N={len(rents)})", flush=True)

    print("재크롤링 완료.", flush=True)


if __name__ == "__main__":
    main()
