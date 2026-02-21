"""One-off script to inspect SUUMO HTML structure for cassette class names."""
import urllib.request
import re
import time

url = (
    "https://suumo.jp/jj/chintai/ichiran/FR301FC001/"
    "?ar=030&bs=040&ta=13&shkr1=03&shkr2=03&shkr3=03&shkr4=03"
    "&fw2=&sngz=&smk=&srch_navi=1&page=1&pc=50&md=04&sc=13123"
)
headers = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36",
    "Accept-Language": "ja,en;q=0.9",
    "Accept": "text/html,application/xhtml+xml",
}
req = urllib.request.Request(url, headers=headers)
time.sleep(2.0)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        html = resp.read().decode("utf-8", errors="replace")
    print(f"HTML len: {len(html)}")

    # Find class names containing cassette or listing-related keywords
    classes = re.findall(r'class="([^"]*(?:cassette|item|property|bukken|listing)[^"]*)"', html)
    unique = sorted(set(classes))
    print("Suspected listing-item classes:")
    for c in unique[:30]:
        print(" ", repr(c))

    # Also check what div class patterns appear near 万円 (rent amounts)
    rent_contexts = re.findall(r'class="([^"]+)"[^<]{0,200}万円', html)
    print("\nClasses near 万円:")
    for c in sorted(set(rent_contexts))[:20]:
        print(" ", repr(c))

except Exception as e:
    print("Error:", e)
