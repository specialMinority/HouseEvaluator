
import requests

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

url = "https://www.chintai.net/tokyo/area/13101/list/"
r = requests.get(url, headers=HEADERS)
with open("chiyoda_list.html", "w", encoding="utf-8") as f:
    f.write(r.text)
print("Saved chiyoda_list.html")
