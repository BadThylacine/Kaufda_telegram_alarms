import requests
from datetime import datetime

# --- CONFIGURATION ---
TELEGRAM_TOKEN = ""
CHAT_ID = "829548526"
KEYWORDS = ["cheddar", "lachs", "pastrami", "parmesan","tony's"]  # customize
# KEYWORDS = ["lachs"]  # customize
LAT, LNG = 52.4669, 13.4299  # Berlin coords, adjust if needed
SIZE = 25
SAVE_FILE = "last_results.json"
MAX_PRICE = 5.0  # euro limit

def parse_price(value):
    """Parse messy price strings like '€4,99€' or '4.99€€' into float."""
    if not value:
        return None

    # Handle numeric inputs directly
    if isinstance(value, (int, float)):
        return float(value)

    if isinstance(value, str):
        # Remove all euro symbols, spaces, and strange characters
        cleaned = re.sub(r"[^\d,.\s]", "", value)  # keep only digits, dots, commas, spaces
        cleaned = cleaned.replace(",", ".").strip()

        # Extract the first valid number
        match = re.search(r"\d+(\.\d+)?", cleaned)
        if match:
            try:
                return float(match.group(0))
            except ValueError:
                return None
    return None

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


    resp = requests.get(url, headers=headers, params=params)
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
            "publisherName": content.get("publisherName", "none"),
            "name": product.get("name", "none"),
            "brandName": product.get("brand", {}).get("name", "none"),
            "price": deal.get("min", "none"),
            "endDate": end_date_str or "none",
        })

    return results

# --- SEND TO TELEGRAM ---
def send_to_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage"
    payload = {"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"}
    requests.post(url, data=payload)

# --- MAIN LOGIC ---
def main():
    all_results = []

    for keyword in KEYWORDS:
        offers = fetch_offers(keyword)

        filtered_offers = []
        for o in offers:
            price_val = parse_price(o.get("price"))
            if price_val is not None and price_val <= MAX_PRICE:
                # Always normalize to "X.XX€" format
                o["price"] = f"{price_val:.2f}€"
                filtered_offers.append(o)

        # Sort alphabetically by supermarket
        filtered_offers = sorted(filtered_offers, key=lambda o: o.get("publisherName", "").lower())

        if filtered_offers:
            text_lines = [f"🔎 <b>{keyword.capitalize()}</b>"]
            for o in filtered_offers:
                publisher = o.get("publisherName", "none")
                brand = o.get("brandName", "none")
                name = o.get("name", "none")
                price = o.get("price", "none")
                end_date = o.get("endDate", "none")

                # Highlight REWE offers
                if publisher.lower() == "rewe":
                    line = f"🛒 <b>{publisher}</b> — {brand} {name}: {price}€ (until {end_date}) 💥"
                else:
                    line = f"🛒 {publisher} — {brand} {name}: {price}€ (until {end_date})"

                text_lines.append(line)
            all_results.append("\n".join(text_lines))

    # Combine everything into one message
    if all_results:
        message = (
            f"🗓 <b>Kaufda Weekly Offers ({datetime.now():%d.%m.%Y})</b>\n\n"
            + "\n\n".join(all_results)
        )
        # send_to_telegram(message)
        print(message)
    else:
        print("No offers found.")


if __name__ == "__main__":
    main()