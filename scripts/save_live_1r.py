
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

url = "https://www.chintai.net/tokyo/area/13101/list/?m=0"
print(f"Fetching {url}...")
r = requests.get(url, headers=HEADERS, timeout=15)
if r.status_code == 200:
    with open("debug_live_1r.html", "w", encoding="utf-8") as f:
        f.write(r.text)
    print("Saved debug_live_1r.html")
else:
    print(f"Failed: {r.status_code}")
