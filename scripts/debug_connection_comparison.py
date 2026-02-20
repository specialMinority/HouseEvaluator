
import requests
import time

UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"

FULL_HEADERS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.7",
    "Accept-Encoding": "gzip, deflate, br",
    "Accept-Language": "ja,en-US;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Cache-Control": "max-age=0",
}

def check(url, label):
    print(f"--- Checking {label} ---")
    print(f"URL: {url}")
    try:
        r = requests.get(url, headers=FULL_HEADERS, timeout=15)
        print(f"Status: {r.status_code}")
        if r.status_code == 200:
            if "awsWaf" in r.text or "challenge.js" in r.text:
                print("[FAIL] Blocked by AWS WAF")
            elif "average_rent" in r.text or "平均賃料" in r.text or "家賃相場" in r.text:
                print("[SUCCESS] Content seems accessible")
            else:
                print("[INFO] accessible but keyword not found immediately")
                print(r.text[:200])
        else:
             print("[FAIL] Status code error")
    except Exception as e:
        print(f"Error: {e}")
    print("\n")

# LIFULL
check("https://www.homes.co.jp/chintai/tokyo/chiyoda-ku/list/?po=wood&m=1r", "LIFULL (Slug)")

# CHINTAI
# Guessing URL pattern for CHINTAI: https://www.chintai.net/tokyo/list/?...
# Need to search/verify CHINTAI structure.
# Trying a generic CHINTAI Tokyo page.
check("https://www.chintai.net/tokyo/", "CHINTAI Tokyo Top")
