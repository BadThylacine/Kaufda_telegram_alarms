import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

KEYWORDS = [k.strip() for k in os.getenv("KEYWORDS", "milch").split(",") if k.strip()]
MAX_PRICE = float(os.getenv("MAX_PRICE", "4.0"))
SEARCH_LAT = float(os.getenv("SEARCH_LAT", "52.4669"))
SEARCH_LNG = float(os.getenv("SEARCH_LNG", "13.4299"))
STATE_FILE = os.getenv("STATE_FILE", "kaufda_state.json")
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
DEDUP_ENABLED = os.getenv("DEDUP_ENABLED", "false").lower() == "true"
HIGHLIGHT_PUBLISHERS = {
    p.strip().lower() for p in os.getenv("HIGHLIGHT_PUBLISHERS", "rewe").split(",") if p.strip()
}
REQUEST_TIMEOUT = 10  # seconds

KAUFDA_SEARCH_URL = "https://www.kaufda.de/api/search"
TELEGRAM_SEND_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"

# Persistent session for HTTP connection pooling
session = requests.Session()
session.headers.update({
    "accept": "application/json",
    "delivery_channel": "dest.kaufda",
    "user_platform_category": "desktop.web.browser",
    "user_platform_os": "windows",
})


class ConfigurationError(Exception):
    """Raised when required configuration values are missing or invalid."""


class KaufdaAPIError(Exception):
    """Raised when a Kaufda API request or response cannot be processed."""


def validate_config() -> None:
    errors = []
    if not KEYWORDS:
        errors.append("KEYWORDS list is empty")
    if MAX_PRICE <= 0:
        errors.append(f"MAX_PRICE must be positive, got {MAX_PRICE}")
    if TELEGRAM_TOKEN and not CHAT_ID:
        errors.append("CHAT_ID required when TELEGRAM_TOKEN is set")

    if errors:
        raise ConfigurationError(f"Configuration errors: {', '.join(errors)}")

    logger.info("Configuration valid - Keywords: %s, Max price: %s EUR", KEYWORDS, MAX_PRICE)


# -----------------------------------------------------------------------------
# API & Data Helpers
# -----------------------------------------------------------------------------

def parse_price(price_value: Any) -> Optional[float]:
    if isinstance(price_value, (int, float)):
        return round(float(price_value), 2)
    if isinstance(price_value, str) and (match := re.search(r"\d+[.,]\d+", price_value)):
        return round(float(match.group(0).replace(",", ".")), 2)
    return None


def fetch_offers(keyword: str) -> Tuple[List[Dict[str, Any]], bool]:
    """Fetch and filter offers for a single keyword.

    Returns (offers, success). success=False means the fetch itself failed
    (network/API error), which must not be confused with "genuinely zero
    offers found" — the two look identical if you only check the list.
    """
    params = {"query": keyword, "lat": SEARCH_LAT, "lng": SEARCH_LNG}
    try:
        resp = session.get(KAUFDA_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        contents = resp.json().get("searchResults", {}).get("contents", {}).get("offers", [])
    except requests.exceptions.Timeout:
        logger.error("Request timeout for keyword '%s'", keyword)
        return [], False
    except requests.exceptions.RequestException as exc:
        logger.error("API request failed for '%s': %s", keyword, exc)
        return [], False
    except ValueError as exc:
        logger.error("Invalid JSON response for '%s': %s", keyword, exc)
        return [], False

    results = []
    for item in contents:
        try:
            price = parse_price(item.get("prices", {}).get("mainPrice"))
            if price is not None and price <= MAX_PRICE:
                results.append({
                    "publisher": item.get("publisherName", "Unknown"),
                    "brand": item.get("title", ""),
                    "price": price,
                })
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Malformed item data, skipping: %s", exc)
            continue

    logger.info("Found %d offers for '%s'", len(results), keyword)
    return sorted(results, key=lambda x: x["publisher"].lower()), True


# -----------------------------------------------------------------------------
# Formatting & State Management
# -----------------------------------------------------------------------------

def offer_key(offer: Dict) -> str:
    return f"{offer.get('publisher')}|{offer.get('brand')}|{offer.get('price')}".lower()


def format_message(offers_by_kw: Dict[str, List[Dict]]) -> str:
    sections = []
    for kw, offers in offers_by_kw.items():
        if not offers:
            continue
        lines = [f"Search: <b>{kw.capitalize()}</b>"]
        for o in offers:
            marker = f" [{o['publisher'].upper()}]" if o["publisher"].lower() in HIGHLIGHT_PUBLISHERS else ""
            lines.append(f"- <b>{o['publisher']}</b> - {o['brand']} - {o['price']:.2f} EUR{marker}")
        sections.append("\n".join(lines))

    return f"Kaufda Offers ({datetime.now():%d.%m.%Y})\n\n" + "\n\n".join(sections) if sections else ""


def send_to_telegram(message: str) -> bool:
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping send")
        return False
    try:
        url = TELEGRAM_SEND_URL_TEMPLATE.format(token=TELEGRAM_TOKEN)
        resp = session.post(
            url,
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Message sent to Telegram successfully")
        return True
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


def load_state() -> Optional[Dict[str, List[Dict]]]:
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as exc:
        logger.warning("Could not load state file: %s", exc)
        return None


def save_state(offers_by_keyword: Dict[str, List[Dict]]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(offers_by_keyword, f, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Could not save state file: %s", exc)


def diff_offers(
    current: Dict[str, List[Dict]],
    previous: Dict[str, List[Dict]],
) -> Dict[str, List[Dict]]:
    """Return offers present in `current` but not in `previous`, per keyword.

    Builds the "seen" set once per keyword rather than re-deriving it on
    every comprehension pass.
    """
    result = {}
    for kw, offers in current.items():
        seen = {offer_key(o) for o in previous.get(kw, [])}
        new_offers = [o for o in offers if offer_key(o) not in seen]
        if new_offers:
            result[kw] = new_offers
    return result


# -----------------------------------------------------------------------------
# Main Execution
# -----------------------------------------------------------------------------

def main() -> int:
    try:
        validate_config()

        offers_by_kw: Dict[str, List[Dict]] = {}
        failed_keywords: List[str] = []

        with ThreadPoolExecutor(max_workers=min(10, len(KEYWORDS))) as executor:
            for keyword, (offers, success) in zip(KEYWORDS, executor.map(fetch_offers, KEYWORDS)):
                if success:
                    offers_by_kw[keyword] = offers
                else:
                    failed_keywords.append(keyword)

        if DEDUP_ENABLED:
            previous_state = load_state()
            save_state(offers_by_kw)
            if previous_state is None:
                logger.info("No previous state found, reporting all current offers")
                to_report = offers_by_kw
            else:
                to_report = diff_offers(offers_by_kw, previous_state)
        else:
            to_report = offers_by_kw

        if not to_report:
            msg = f"✅ No new deals since last check ({datetime.now():%d.%m.%Y})."
            logger.info("No new deals found")
        else:
            msg = format_message(to_report) or "No offers found matching your criteria."

        print(msg)
        send_to_telegram(msg)

        if failed_keywords:
            logger.warning("Failed to fetch offers for: %s", ", ".join(failed_keywords))

        return 0

    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())
