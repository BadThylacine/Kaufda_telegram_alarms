import requests
import json
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_TOKEN = "your_telegram_bot_token"
CHAT_ID = "your_chat_id"
KEYWORDS = ["cheddar", "lachs", "pastrami", "tony"]  # customize
LAT, LNG = 52.4669, 13.4299  # Berlin coords, adjust if needed
SIZE = 25
SAVE_FILE = "last_results.json"

# --- FETCH DATA ---
def fetch_offers(keyword):
    url = "https://www.kaufda.de/webapp/api/slots/offerSearch"
    params = {
        "searchQuery": keyword,
        "lat": 52.466892718012886,
        "lng": 13.429997592151873,
        "size": 25
    }

    headers = {
        "accept": "application/json",
        "delivery_channel": "dest.kaufda",
        "user_platform_category": "desktop.web.browser",
        "user_platform_os": "windows",
    }


    resp = requests.get(url, headers=headers, cookies=cookies, params=params)
    resp.raise_for_status()
    data = resp.json()
    contents = data.get("_embedded", {}).get("contents", [])

    results = []
    for item in contents:
        content = item["content"]

        publisher_name = content["publisherName"]
        product = content["products"][0]
        deal = content["deals"][0]
        profile = content["publicationProfiles"][0]
        validity = profile["validity"]

        # Convert ISO date string → datetime → "DD.MM.YYYY"
        end_date = datetime.strptime(validity["endDate"], "%Y-%m-%dT%H:%M:%S.%f%z")
        end_date_str = end_date.strftime("%d.%m.%Y")

        results.append({
            "publisherName": publisher_name,
            "name": product["name"],
            "brandName": product["brand"]["name"],
            "price": deal["min"],
            "endDate": end_date_str,
        })

    return results

# --- SEND TO TELEGRAM ---
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    requests.post(url, data=payload)

# --- MAIN LOGIC ---
def main():
    all_new = []
    last_results = {}
    try:
        last_results = json.load(open(SAVE_FILE))
    except FileNotFoundError:
        pass

    current_results = {}

    for keyword in KEYWORDS:
        offers = fetch_offers(keyword)
        current_results[keyword] = [o["id"] for o in offers]

        new_offers = [
            o for o in offers
            if o["id"] not in last_results.get(keyword, [])
        ]

        if new_offers:
            text_lines = [f"🔎 <b>{keyword}</b>"]
            for offer in new_offers:
                title = offer.get("title", "No title")
                retailer = offer.get("retailer", {}).get("name", "")
                link = offer.get("deeplinkUrl", "#")
                text_lines.append(f"🛒 <a href='{link}'>{title}</a> ({retailer})")
            all_new.append("\n".join(text_lines))

    # Only send if there’s something new
    if all_new:
        message = f"🗓 <b>Kaufda Weekly Offers ({datetime.now():%d.%m.%Y})</b>\n\n" + "\n\n".join(all_new)
        # send_to_telegram(message)

    # Save for next run
    with open(SAVE_FILE, "w") as f:
        json.dump(current_results, f, indent=2)

if __name__ == "__main__":
    main()
