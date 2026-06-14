import json
import logging
import os
import re
import sys
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from typing import Any, Dict, List, Optional

import requests


# -----------------------------------------------------------------------------
# Configuration
# -----------------------------------------------------------------------------

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s")
logger = logging.getLogger(__name__)

TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
KEYWORDS = os.getenv("KEYWORDS", "lachs").split(",")
MAX_PRICE = float(os.getenv("MAX_PRICE", "4.0"))
SEARCH_LAT = float(os.getenv("SEARCH_LAT", "52.4669"))
SEARCH_LNG = float(os.getenv("SEARCH_LNG", "13.4299"))
STATE_FILE = os.getenv("STATE_FILE", "kaufda_state.json")
REQUEST_TIMEOUT = 10  # seconds

KAUFDA_SEARCH_URL = "https://www.kaufda.de/api/search"
TELEGRAM_SEND_URL_TEMPLATE = "https://api.telegram.org/bot{token}/sendMessage"


class KaufdaAPIError(Exception):
    """Raised when a Kaufda API request or response cannot be processed."""


class ConfigurationError(Exception):
    """Raised when required configuration values are missing or invalid."""


# -----------------------------------------------------------------------------
# Validation and parsing
# -----------------------------------------------------------------------------

def validate_config() -> None:
    """Validate the runtime configuration before any network work starts."""
    errors = []

    if not KEYWORDS or (len(KEYWORDS) == 1 and not KEYWORDS[0].strip()):
        errors.append("KEYWORDS list is empty")

    if MAX_PRICE <= 0:
        errors.append(f"MAX_PRICE must be positive, got {MAX_PRICE}")

    if TELEGRAM_TOKEN and not CHAT_ID:
        errors.append("CHAT_ID required when TELEGRAM_TOKEN is set")

    if errors:
        raise ConfigurationError(f"Configuration errors: {', '.join(errors)}")

    logger.info("Configuration valid - Keywords: %s, Max price: %s EUR", KEYWORDS, MAX_PRICE)


def parse_price(price_value: Any) -> Optional[float]:
    """Convert a raw price value into a rounded float, or return None."""
    if price_value is None:
        return None

    if isinstance(price_value, (int, float)):
        return round(float(price_value), 2)

    if isinstance(price_value, str):
        price_match = re.search(r"\d+[.,]\d+", price_value)
        if price_match:
            try:
                return round(float(price_match.group(0).replace(",", ".")), 2)
            except ValueError:
                logger.warning("Failed to convert price: %s", price_value)
                return None

    return None


# -----------------------------------------------------------------------------
# Kaufda API access
# -----------------------------------------------------------------------------

def fetch_offers(keyword: str) -> List[Dict[str, str]]:
    """Fetch offers for one keyword and return the filtered results."""
    params = {"query": keyword, "lat": SEARCH_LAT, "lng": SEARCH_LNG}
    headers = {
        "accept": "application/json",
        "delivery_channel": "dest.kaufda",
        "user_platform_category": "desktop.web.browser",
        "user_platform_os": "windows",
    }

    try:
        logger.info("Fetching offers for keyword: %s", keyword)
        resp = requests.get(
            KAUFDA_SEARCH_URL,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
    except requests.exceptions.Timeout:
        raise KaufdaAPIError(f"Request timeout for keyword '{keyword}'")
    except requests.exceptions.RequestException as exc:
        raise KaufdaAPIError(f"API request failed for '{keyword}': {exc}") from exc
    except ValueError as exc:
        raise KaufdaAPIError(f"Invalid JSON response for '{keyword}': {exc}") from exc

    results: List[Dict[str, str]] = []
    contents = data.get("searchResults", {}).get("contents", {}).get("offers", {})

    if not contents:
        logger.info("No results found for keyword: %s", keyword)
        return []

    for item in contents:
        try:
            price_raw = item.get("prices", {}).get("mainPrice")
            price = parse_price(price_raw)

            if price is None:
                logger.debug("Skipping item with invalid price: %s", price_raw)
                continue

            if price > MAX_PRICE:
                logger.debug("Skipping item over max price: %s EUR", price)
                continue

            results.append(
                {
                    "publisher": item.get("publisherName", "Unknown"),
                    "brand": item.get("title", ""),
                    "price": f"{price} EUR",
                }
            )
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning("Malformed item data, skipping: %s", exc)
            continue

    logger.info("Found %s offers for '%s'", len(results), keyword)
    return sorted(results, key=lambda x: x["publisher"].lower())


def format_message(offers_by_keyword: Dict[str, List[Dict]]) -> str:
    """Render the current offer set into a Telegram-friendly HTML message."""
    sections = []

    for keyword, offers in offers_by_keyword.items():
        if not offers:
            continue

        lines = [f"Search: <b>{keyword.capitalize()}</b>"]
        for offer in offers:
            publisher = offer["publisher"]
            brand = offer["brand"]
            price = offer["price"]
            marker = " [REWE]" if publisher.lower() == "rewe" else ""
            lines.append(f"- <b>{publisher}</b> - {brand} - {price}{marker}")

        sections.append("\n".join(lines))

    if not sections:
        return ""

    return f"Kaufda Offers ({datetime.now():%d.%m.%Y})\n\n" + "\n\n".join(sections)


def send_to_telegram(message: str) -> bool:
    """Send a formatted message to Telegram when credentials are configured."""
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping send")
        return False

    try:
        resp = requests.post(
            TELEGRAM_SEND_URL_TEMPLATE.format(token=TELEGRAM_TOKEN),
            data={"chat_id": CHAT_ID, "text": message, "parse_mode": "HTML"},
            timeout=REQUEST_TIMEOUT,
        )
        resp.raise_for_status()
        logger.info("Message sent to Telegram successfully")
        return True
    except requests.exceptions.RequestException as exc:
        logger.error("Failed to send Telegram message: %s", exc)
        return False


# -----------------------------------------------------------------------------
# State and deduplication
# -----------------------------------------------------------------------------

def offer_key(offer: Dict) -> str:
    """Build a stable key for one offer so it can be compared across runs."""
    return "|".join(
        [
            offer.get("publisher", "").lower(),
            offer.get("brand", "").lower(),
            offer.get("price", "").lower(),
        ]
    )


def load_state() -> Optional[Dict[str, List[Dict]]]:
    """Load the last saved offer snapshot from disk, if it exists."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as file_handle:
            return json.load(file_handle)
    except FileNotFoundError:
        return None
    except Exception as exc:
        logger.warning("Could not load state file: %s", exc)
        return None


def save_state(offers_by_keyword: Dict[str, List[Dict]]) -> None:
    """Persist the current offer snapshot for the next comparison run."""
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as file_handle:
            json.dump(offers_by_keyword, file_handle, ensure_ascii=False, indent=2)
    except Exception as exc:
        logger.warning("Could not save state file: %s", exc)


def diff_offers(
    current: Dict[str, List[Dict]],
    previous: Dict[str, List[Dict]],
) -> Dict[str, List[Dict]]:
    """Return offers that appear in the current run but not in the previous one."""
    result = {}

    for keyword, offers in current.items():
        seen = {offer_key(offer) for offer in previous.get(keyword, [])}
        new_offers = [offer for offer in offers if offer_key(offer) not in seen]
        if new_offers:
            result[keyword] = new_offers

    return result


# -----------------------------------------------------------------------------
# Application flow
# -----------------------------------------------------------------------------

def main() -> int:
    """Run the full fetch, diff, format, and send workflow."""
    try:
        validate_config()

        offers_by_keyword: Dict[str, List[Dict]] = {}
        failed_keywords: List[str] = []

        with ThreadPoolExecutor(max_workers=min(10, len(KEYWORDS))) as executor:
            future_to_keyword = {
                executor.submit(fetch_offers, keyword.strip()): keyword.strip()
                for keyword in KEYWORDS
                if keyword.strip()
            }

            for future in as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                try:
                    offers_by_keyword[keyword] = future.result()
                except KaufdaAPIError as exc:
                    logger.error("%s", exc)
                    failed_keywords.append(keyword)

        """" Deduplicate offers found in the current run."""
        """ Switch the comments within next 9 lines to change the mode"""
        # previous_state = load_state()
        # save_state(offers_by_keyword)
        #
        # if previous_state is None:
        #     logger.info("No previous state found, reporting all current offers")
        #     to_report = offers_by_keyword
        # else:
        #     to_report = diff_offers(offers_by_keyword, previous_state)
        to_report = offers_by_keyword

        if not to_report:
            message = f"✅ No new deals since last check ({datetime.now():%d.%m.%Y})."
            logger.info("No new deals found")
        else:
            message = format_message(to_report) or "No offers found matching your criteria."

        print(message)
        send_to_telegram(message)

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
