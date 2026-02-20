
import requests
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
}

def get_soup(url):
    print(f"Fetching {url}")
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        print(f"Status: {r.status_code} URL: {r.url}")
        return BeautifulSoup(r.text, 'html.parser'), r.url
    except Exception as e:
        print(f"Error: {e}")
        return None, None

def main():
    # 1. Get Tokyo Top to find Chiyoda-ku link
    soup, base_url = get_soup("https://www.chintai.net/tokyo/")
    if not soup: return

    # Find Chiyoda-ku link
    chiyoda_link = None
    for a in soup.find_all('a', href=True):
        if "千代田区" in a.get_text():
            chiyoda_link = urljoin(base_url, a['href'])
            print(f"Found Chiyoda-ku: {chiyoda_link}")
            break
    
    if not chiyoda_link:
        print("Chiyoda-ku link not found!")
        return

    # 2. Go to Chiyoda-ku page (or list page)
    soup, ward_url = get_soup(chiyoda_link)
    if not soup: return
    
    # Check if this is already a list or a condition select page
    # Look for "Average Rent" link
    avg_rent_link = None
    for a in soup.find_all('a', href=True):
        if "家賃相場" in a.get_text():
            avg_rent_link = urljoin(ward_url, a['href'])
            print(f"Found Market Price Link: {avg_rent_link}")
            break
            
    # Look for 1R/1K list link
    list_url = ward_url # Default to current if it's a list
    # Refine to 1R (often via parameter or path)
    # Trying to find a form or link with 'm=1R' or similar
    
    # 3. Test Structure Params on the list URL
    # We suspect 'k' or 'structure' or 'building_type'
    # Let's try appending common params
    
    # Structure codes guess: 
    # Wood: k=1? or cts=1?
    # RC: k=4? 
    # We can't know for sure without seeing a form.
    # Let's check for inputs in the page.
    inputs = soup.find_all('input')
    print("Potential Filter Inputs:")
    for i in inputs[:10]: # Check first 10
        if i.get('name'):
            print(f"  {i.get('name')} = {i.get('value')}")

    # 4. Try fetching with guessed params
    # Common japanese real estate params:
    # m, madori = layout (1R=10, 1K=something)
    # k, kouzou = structure (Wood=1, etc)
    
    # Try to fetch "1R + Wood"
    # m=1R might need mapping.
    
    # If we found Market Price page, fetch it.
    if avg_rent_link:
        soup, _ = get_soup(avg_rent_link)
        if soup:
            # Check table structure
            print("Market Price Page Title:", soup.title.string.strip())
            # Dump first few table rows
            tables = soup.find_all('table')
            for t in tables:
                print("Table found:", t.get('class'))
                rows = t.find_all('tr')
                for r in rows[:3]:
                    print("  Row:", [c.get_text().strip() for c in r.find_all(['th', 'td'])])

if __name__ == "__main__":
    main()
