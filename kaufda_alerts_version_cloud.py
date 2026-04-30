import os
import sys
import json
import logging
import re
import requests
from datetime import datetime
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Optional

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
KEYWORDS = [kw.strip() for kw in os.getenv("KEYWORDS", "lachs").split(",") if kw.strip()]
MAX_PRICE = float(os.getenv("MAX_PRICE", "4.0"))
SEARCH_LAT = float(os.getenv("SEARCH_LAT", "52.4669"))
SEARCH_LNG = float(os.getenv("SEARCH_LNG", "13.4299"))
SEARCH_SIZE = int(os.getenv("SEARCH_SIZE", "25"))
STATE_FILE = os.getenv("STATE_FILE", "kaufda_state.json")
REQUEST_TIMEOUT = 10

_PRICE_RE = re.compile(r"\d+[.,]\d+")


class KaufdaAPIError(Exception):
    pass


def parse_price(value: object) -> Optional[float]:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        match = _PRICE_RE.search(value)
        if match:
            try:
                return float(match.group(0).replace(",", "."))
            except ValueError:
                logger.warning(f"Failed to convert price: {value}")
    return None


def fetch_offers(keyword: str) -> list[dict]:
    """Fetch and return price-filtered offers from the Kaufda API, sorted by publisher."""
    params = {
        "searchQuery": keyword,
        "lat": SEARCH_LAT,
        "lng": SEARCH_LNG,
        "size": SEARCH_SIZE,
    }
    headers = {
        "accept": "application/json",
        "delivery_channel": "dest.kaufda",
        "user_platform_category": "desktop.web.browser",
        "user_platform_os": "windows",
        "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    }

    try:
        logger.info(f"Fetching offers for keyword: {keyword}")
        resp = requests.get(
            "https://www.kaufda.de/webapp/api/slots/offerSearch",
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        raise KaufdaAPIError(f"Request timeout for keyword '{keyword}'")
    except requests.exceptions.RequestException as e:
        raise KaufdaAPIError(f"API request failed for '{keyword}': {e}")
    except ValueError as e:
        raise KaufdaAPIError(f"Invalid JSON response for '{keyword}': {e}")

    contents = data.get("_embedded", {}).get("contents", [])
    if not contents:
        logger.info(f"No results found for keyword: {keyword}")
        return []

    results = []
    for item in contents:
        try:
            c = item["content"]

            price = parse_price(c.get("deals", [{}])[0].get("min"))
            if price is None:
                continue
            if price > MAX_PRICE:
                continue

            product = c.get("products", [{}])[0]
            profile = c.get("publicationProfiles", [{}])[0]
            end_date_str = profile.get("validity", {}).get("endDate")
            if not end_date_str:
                logger.warning("Missing end date, skipping item")
                continue

            try:
                end_date = datetime.strptime(end_date_str, "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d.%m.%Y")
            except ValueError:
                logger.warning(f"Invalid date format: {end_date_str}")
                continue

            results.append({
                "publisher": c.get("publisherName", "Unknown"),
                "brand": product.get("brand", {}).get("name", ""),
                "name": product.get("name", ""),
                "price": f"{price:.2f}€",
                "endDate": end_date,
            })

        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"Malformed item data, skipping: {e}")

    logger.info(f"Found {len(results)} offers for '{keyword}'")
    return sorted(results, key=lambda x: x["publisher"].lower())


def format_message(offers_by_keyword: dict[str, list[dict]]) -> str:
    sections = []
    for keyword, offers in offers_by_keyword.items():
        if not offers:
            continue
        lines = [f"🔎 <b>{keyword.capitalize()}</b>"]
        for o in offers:
            emoji = " 💥" if o["publisher"].lower() == "rewe" else ""
            lines.append(
                f"🛒 <b>{o['publisher']}</b> — {o['brand']} {o['name']}: "
                f"{o['price']} (until {o['endDate']}){emoji}"
            )
        sections.append("\n".join(lines))

    if not sections:
        return ""

    return f"🗓 <b>Kaufda Offers ({datetime.now():%d.%m.%Y})</b>\n\n" + "\n\n".join(sections)


def send_to_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping send")
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Message sent to Telegram successfully")
        return True
    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {e}")
        return False


def offer_key(offer: dict) -> str:
    """Identity key for deduplication — end date excluded intentionally."""
    return "|".join(offer.get(k, "").lower() for k in ("publisher", "brand", "name", "price"))


def load_state() -> Optional[dict[str, list[dict]]]:
    """Return persisted offers, or None on first run."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Could not load state file: {e}")
        return None


def save_state(offers_by_keyword: dict[str, list[dict]]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(offers_by_keyword, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save state file: {e}")


def diff_offers(current: dict[str, list[dict]], previous: dict[str, list[dict]]) -> dict[str, list[dict]]:
    """Return only offers present in current but absent from previous."""
    result = {}
    for keyword, offers in current.items():
        seen = {offer_key(o) for o in previous.get(keyword, [])}
        new = [o for o in offers if offer_key(o) not in seen]
        if new:
            result[keyword] = new
    return result


def main() -> int:
    try:
        offers_by_keyword: dict[str, list[dict]] = {}
        failed_keywords: list[str] = []

        with ThreadPoolExecutor(max_workers=min(10, len(KEYWORDS))) as executor:
            futures = {executor.submit(fetch_offers, kw): kw for kw in KEYWORDS}
            for future in as_completed(futures):
                keyword = futures[future]
                try:
                    offers_by_keyword[keyword] = future.result()
                except KaufdaAPIError as e:
                    logger.error(e)
                    failed_keywords.append(keyword)

        prev_state = load_state()
        save_state(offers_by_keyword)

        if prev_state is None:
            logger.info("No previous state found, reporting all current offers")
            to_report = offers_by_keyword
        else:
            to_report = diff_offers(offers_by_keyword, prev_state)

        if not to_report:
            message = f"✅ No new deals since last check ({datetime.now():%d.%m.%Y})."
            logger.info("No new deals found")
        else:
            message = format_message(to_report) or "No offers found matching your criteria."

        print(message)
        send_to_telegram(message)

        if failed_keywords:
            logger.warning(f"Failed to fetch offers for: {', '.join(failed_keywords)}")

        return 0

    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
