#!/usr/bin/env python3
"""
FUT Next Production Scraper – writes results to Cloudflare R2.
Runs inside GitHub Actions on a schedule.
"""

import json, time, os, re
from datetime import datetime
from playwright.sync_api import sync_playwright
import boto3

# ========== RARITY PAGE URLS ==========
RARITY_URLS = [
    "https://www.futnext.com/player/rarity/11-0",
    "https://www.futnext.com/player/rarity/65-0",
    "https://www.futnext.com/player/rarity/76-0",
    "https://www.futnext.com/player/rarity/77-0",
    "https://www.futnext.com/player/rarity/126-0",
    "https://www.futnext.com/player/rarity/32-0",
    "https://www.futnext.com/player/rarity/125-0",
    "https://www.futnext.com/player/rarity/124-0",
    "https://www.futnext.com/player/rarity/20-0",
    "https://www.futnext.com/player/rarity/30-0",
    "https://www.futnext.com/player/rarity/149-0",
    "https://www.futnext.com/player/rarity/148-0",
    "https://www.futnext.com/player/rarity/135-0",
    "https://www.futnext.com/player/rarity/111-0",
    "https://www.futnext.com/player/rarity/55-0",
    "https://www.futnext.com/player/rarity/14-0",
    "https://www.futnext.com/player/rarity/15-0",
    "https://www.futnext.com/player/rarity/23-0",
    "https://www.futnext.com/player/rarity/71-0",
    "https://www.futnext.com/player/rarity/170-0",
    "https://www.futnext.com/player/rarity/64-0",
    "https://www.futnext.com/player/rarity/5-0",
    "https://www.futnext.com/player/rarity/155-0",
    "https://www.futnext.com/player/rarity/108-0",
    "https://www.futnext.com/player/rarity/112-0",
    "https://www.futnext.com/player/rarity/49-0",
    "https://www.futnext.com/player/rarity/117-0",
    "https://www.futnext.com/player/rarity/83-0",
    "https://www.futnext.com/player/rarity/85-0",
    "https://www.futnext.com/player/rarity/82-0",
    "https://www.futnext.com/player/rarity/97-0",
    "https://www.futnext.com/player/rarity/96-0",
    "https://www.futnext.com/player/rarity/35-0",
    "https://www.futnext.com/player/rarity/33-0",
    "https://www.futnext.com/player/rarity/157-0",
    "https://www.futnext.com/player/rarity/34-0",
    "https://www.futnext.com/player/rarity/28-0",
    "https://www.futnext.com/player/rarity/8-0",
    "https://www.futnext.com/player/rarity/105-0",
    "https://www.futnext.com/player/rarity/46-0",
    "https://www.futnext.com/player/rarity/27-0",
    "https://www.futnext.com/player/rarity/26-0",
    "https://www.futnext.com/player/rarity/31-0",
    "https://www.futnext.com/player/rarity/50-0",
    "https://www.futnext.com/player/rarity/168-0",
    "https://www.futnext.com/player/rarity/151-0",
    "https://www.futnext.com/player/rarity/22-0",
    "https://www.futnext.com/player/rarity/150-0",
    "https://www.futnext.com/player/rarity/12-0",
    "https://www.futnext.com/player/rarity/3-0",
    "https://www.futnext.com/player/rarity/72-0",
    "https://www.futnext.com/player/rarity/161-0",
]

DELAY_BETWEEN_PAGES = 6        # seconds
HEADLESS = True

# ========== PRICE PARSER ==========
def parse_price(text):
    if not text:
        return None
    text = str(text).strip().upper().replace(",", "").replace(" ", "")
    if text in ("N/A", "-", "—", ""):
        return None
    try:
        if "M" in text:
            return int(float(text.replace("M", "")) * 1_000_000)
        if "K" in text:
            return int(float(text.replace("K", "")) * 1_000)
        return int(text)
    except ValueError:
        return None

# ========== RARITY EXTRACTION ==========
def extract_rarity_name(page, url):
    """Extract the promo/rarity name from the page heading or URL."""
    # Try the text just before "Total Players"
    try:
        body = page.inner_text("body")
        match = re.search(r'(.+?)\s*Total Players', body)
        if match:
            return match.group(1).strip()
    except:
        pass
    # Fallback: extract from URL last segment
    return url.rstrip('/').split('/')[-1]

# ========== PLAYER PARSER ==========
def parse_players_from_text(text):
    players = []
    price_pattern = re.compile(
        r'(\d{1,3}(?:,\d{3})*(?:\.\d)?[KkMm]?)'
    )
    rating_pos_pattern = re.compile(
        r'(\d{2})\s+([A-Z]{2,3})\s+'
    )
    for match in price_pattern.finditer(text):
        price_str = match.group(1)
        if not (',' in price_str or 'K' in price_str.upper() or 'M' in price_str.upper()):
            continue
        price = parse_price(price_str)
        if price is None:
            continue
        after_price = text[match.end():match.end()+50]
        rp_match = rating_pos_pattern.search(after_price)
        if not rp_match:
            continue
        rating = int(rp_match.group(1))
        position = rp_match.group(2)
        after_pos = after_price[rp_match.end():].strip()
        name_end = re.search(r'\bPAC\b|\bSHO\b|\bPAS\b|\bDRI\b|\bDEF\b|\bPHY\b|\b\d{1,3}(?:,\d{3})*\b', after_pos)
        if name_end:
            name = after_pos[:name_end.start()].strip()
        else:
            name = after_pos[:50].strip()
        name = name.rstrip(' -')
        if not name:
            continue
        players.append({
            "name": name,
            "price": price,
            "rating": rating,
            "position": position,
        })
    return players

# ========== R2 UPLOAD ==========
def upload_to_r2(data):
    s3 = boto3.client(
        's3',
        endpoint_url=os.environ['R2_ENDPOINT'],
        aws_access_key_id=os.environ['R2_ACCESS_KEY'],
        aws_secret_access_key=os.environ['R2_SECRET_KEY'],
        region_name='auto'
    )
    key = f"futnext-scrapes/{datetime.now().strftime('%Y-%m-%dT%H-%M-%S')}.json"
    s3.put_object(
        Bucket=os.environ['R2_BUCKET'],
        Key=key,
        Body=json.dumps(data, ensure_ascii=False),
        ContentType='application/json'
    )
    print(f"Uploaded {len(data['players'])} players to R2: {key}")

# ========== MAIN ==========
def main():
    all_players = []
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=HEADLESS)
        page = browser.new_page()
        for url in RARITY_URLS:
            print(f"Scraping {url}")
            page.goto(url, wait_until="networkidle", timeout=60000)
            time.sleep(3)  # extra JS render time
            rarity = extract_rarity_name(page, url)
            text = page.inner_text("body")
            players = parse_players_from_text(text)
            for pl in players:
                pl["rarity"] = rarity
                pl["source"] = "futnext"
                pl["platform"] = "ps"
            all_players.extend(players)
            print(f"  {len(players)} players ({rarity})")
            time.sleep(DELAY_BETWEEN_PAGES)
        browser.close()

    # Deduplicate by (name, rarity)
    seen = set()
    unique = []
    for p in all_players:
        key = (p["name"], p["rarity"])
        if key not in seen:
            seen.add(key)
            unique.append(p)

    data = {
        "scraped_at": datetime.now().isoformat(),
        "total": len(unique),
        "players": unique
    }
    upload_to_r2(data)

if __name__ == "__main__":
    main()