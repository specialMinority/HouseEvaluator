
import requests
from bs4 import BeautifulSoup
import time
import re

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

# Chiyoda-ku (13101)
BASE_URL = "https://www.chintai.net/tokyo/area/13101/list/"

def get_count_and_check(params, label):
    print(f"--- Testing {label}: {params} ---")
    try:
        r = requests.get(BASE_URL, params=params, headers=HEADERS, timeout=10)
        soup = BeautifulSoup(r.text, 'html.parser')
        
        # Get count
        count = "Unknown"
        
        # Try finding in headline text "X件の物件が見つかりました"
        headline = soup.find(class_='headlineText')
        if headline:
            text = headline.get_text()
            m = re.search(r'([0-9,]+)件', text)
            if m:
                count = m.group(1)
        
        print(f"Count: {count}")
        
    except Exception as e:
        print(f"Error: {e}")
    time.sleep(1)

# Baseline
get_count_and_check({}, "No Filter")

# Test 'b' (Building Type) - 1:Mansion, 2:Apartment, 3:House
get_count_and_check({"b": "1"}, "b=1 (Mansion)") 
get_count_and_check({"b": "2"}, "b=2 (Apt)") 
get_count_and_check({"b": "3"}, "b=3 (House)")

# Test 'k' (Structure?) in combination with b?
# In source:
# k=Structure is often used.
# "k" checkbox value="1" (checked by default?) -> "Mgmt fee included" in source label!
# Wait, input name="k" id="k_" value="1" is "共益費･管理費を含む" (Mgmt fee included)
# Structure might be something else?

# Let's check "ts" (Tatemono Shubetsu?) or "kouzou"?
# From source, there isn't a clear structure filter in the sidebar "mod_search".
# "建物種別" (Building Type) is in "mod_search". 
# It says "アパート/マンション/一戸建て". This corresponds to 'b' param usually.

# If 'b' controls "Mansion" vs "Apartment", we can use that as proxy for RC vs Wood?
# Mansion -> RC/SRC/Steel
# Apartment -> Wood/Light Steel
# This is a common heuristic in Japan.

# Let's verify counts for b=1, b=2, b=3.
