
import requests
import sys

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36",
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
    "Referer": "https://www.homes.co.jp/"
}

def check(url, filename):
    print(f"Checking {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        print(f"Status: {r.status_code}")
        print(f"Headers: {r.headers}")
        with open(filename, "w", encoding="utf-8") as f:
            f.write(r.text)
        print(f"Saved to {filename}")
    except Exception as e:
        print(f"Error: {e}")

# Adachi-ku (More likely to have wood)
check("https://www.homes.co.jp/chintai/tokyo/13121/list/?po=wood&m=1k", "debug_adachi.html")
# Chiyoda-ku Slug
check("https://www.homes.co.jp/chintai/tokyo/chiyoda-ku/list/?po=wood&m=1r", "debug_chiyoda_slug.html")
# JIS Code Chiyoda
check("https://www.homes.co.jp/chintai/tokyo/13101/list/?po=wood&m=1r", "debug_chiyoda_jis.html")
