
import requests
from bs4 import BeautifulSoup
import time

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}


URLS = [
    ("Base", "https://www.chintai.net/tokyo/area/13101/list/"),
    ("Query m=0", "https://www.chintai.net/tokyo/area/13101/list/?m=0"),
    ("Path 1r", "https://www.chintai.net/tokyo/area/13101/list/1r/"),
    ("Path 1r + Query b=1", "https://www.chintai.net/tokyo/area/13101/list/1r/?b=1"),
    ("Query m=0&b=1", "https://www.chintai.net/tokyo/area/13101/list/?m=0&b=1")
]

for label, url in URLS:
    print(f"--- Fetching {label}: {url} ---")
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        items = soup.find_all(class_='cassette_item')
        print(f"Status: {r.status_code}, Length: {len(r.text)}, Items: {len(items)}")
        
        # Check canonical
        can = soup.find("link", {"rel": "canonical"})
        if can:
            print(f"Canonical: {can['href']}")
            
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(2)

