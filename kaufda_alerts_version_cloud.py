import os
import sys
import json
import logging
import requests
import re
from datetime import datetime
from typing import List, Dict, Optional
from concurrent.futures import ThreadPoolExecutor, as_completed

# --- CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Default configuration with environment variable fallback in case you forgot to add yours in github variables
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
# KEYWORDS = os.getenv("KEYWORDS", "lachs").split(",")
KEYWORDS = ['barenmarke', 'cheddar', 'lachs', 'landliebe']
MAX_PRICE = float(os.getenv("MAX_PRICE", "4.0"))
SEARCH_LAT = float(os.getenv("SEARCH_LAT", "52.4669"))
SEARCH_LNG = float(os.getenv("SEARCH_LNG", "13.4299"))
STATE_FILE = os.getenv("STATE_FILE", "kaufda_state.json")
REQUEST_TIMEOUT = 10  # seconds


class KaufdaAPIError(Exception):
    """Custom exception for Kaufda API errors"""
    pass


class ConfigurationError(Exception):
    """Custom exception for configuration errors"""
    pass


def validate_config() -> None:
    """Validate required configuration"""
    errors = []

    if not KEYWORDS or (len(KEYWORDS) == 1 and not KEYWORDS[0].strip()):
        errors.append("KEYWORDS list is empty")

    if MAX_PRICE <= 0:
        errors.append(f"MAX_PRICE must be positive, got {MAX_PRICE}")

    if TELEGRAM_TOKEN and not CHAT_ID:
        errors.append("CHAT_ID required when TELEGRAM_TOKEN is set")

    if errors:
        raise ConfigurationError(f"Configuration errors: {', '.join(errors)}")

    logger.info(f"Configuration valid - Keywords: {KEYWORDS}, Max price: {MAX_PRICE}€")


def parse_price(price_value: any) -> Optional[float]:
    """
    Parse price from various formats to float

    Args:
        price_value: Price in any format (str, int, float)

    Returns:
        float or None if parsing fails
    """
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
                logger.warning(f"Failed to convert price: {price_value}")
                return None

    return None


def fetch_offers(keyword: str) -> List[Dict[str, str]]:
    """
    Fetch and filter offers from Kaufda API

    Args:
        keyword: Search keyword

    Returns:
        List of filtered and sorted offers

    Raises:
        KaufdaAPIError: If API request fails
    """

    url = "https://www.kaufda.de/api/search"
    params = {
        "query": keyword,
        "lat": SEARCH_LAT,
        "lng": SEARCH_LNG
    }
    headers = {
        "accept": "application/json",
        "delivery_channel": "dest.kaufda",
        "user_platform_category": "desktop.web.browser",
        "user_platform_os": "windows",
    }

    try:
        logger.info(f"Fetching offers for keyword: {keyword}")
        resp = requests.get(
            url,
            params=params,
            headers=headers,
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        data = resp.json()

    except requests.exceptions.Timeout:
        raise KaufdaAPIError(f"Request timeout for keyword '{keyword}'")
    except requests.exceptions.RequestException as e:
        raise KaufdaAPIError(f"API request failed for '{keyword}': {str(e)}")
    except ValueError as e:
        raise KaufdaAPIError(f"Invalid JSON response for '{keyword}': {str(e)}")

    # Parse results
    results = []
    contents = data.get("searchResults", {}).get("contents", {}).get("offers", {})

    if not contents:
        logger.info(f"No results found for keyword: {keyword}")
        return []

    for item in contents:
        try:
            # Extract and validate price
            price_raw = item.get("prices", {}).get("mainPrice")
            price = parse_price(price_raw)

            if price is None:
                logger.debug(f"Skipping item with invalid price: {price_raw}")
                continue

            if price > MAX_PRICE:
                logger.debug(f"Skipping item over max price: {price}€")
                continue

            results.append({
                "publisher": item.get("publisherName", "Unknown"),
                "brand": item.get("title", ""),
                "price": f"{price}€",
            })

        except (KeyError, IndexError, TypeError) as e:
            logger.warning(f"Malformed item data, skipping: {str(e)}")
            continue

    logger.info(f"Found {len(results)} offers for '{keyword}'")
    return sorted(results, key=lambda x: x["publisher"].lower())


def format_message(offers_by_keyword: Dict[str, List[Dict]]) -> str:
    """
    Format offers into Telegram message

    Args:
        offers_by_keyword: Dictionary mapping keywords to offer lists

    Returns:
        Formatted HTML message
    """
    all_results = []

    for keyword, offers in offers_by_keyword.items():
        if not offers:
            continue

        lines = [f"🔎 <b>{keyword.capitalize()}</b>"]
        for o in offers:
            emoji = "💥" if o["publisher"].lower() == "rewe" else ""
            lines.append(
                f"🛒 <b>{o['publisher']}</b> — {o['brand']} : "
                f"{o['price']} {emoji}".strip()
            )
        all_results.append("\n".join(lines))

    if not all_results:
        return ""

    return (
            f"🗓 <b>Kaufda Offers ({datetime.now():%d.%m.%Y})</b>\n\n"
            + "\n\n".join(all_results)
    )


def send_to_telegram(message: str) -> bool:
    """
    Send message to Telegram

    Args:
        message: Message text to send

    Returns:
        True if successful, False otherwise
    """
    if not TELEGRAM_TOKEN or not CHAT_ID:
        logger.warning("Telegram credentials not configured, skipping send")
        return False

    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_TOKEN}/sendMessage",
            data={
                "chat_id": CHAT_ID,
                "text": message,
                "parse_mode": "HTML"
            },
            timeout=REQUEST_TIMEOUT
        )
        resp.raise_for_status()
        logger.info("Message sent to Telegram successfully")
        return True

    except requests.exceptions.RequestException as e:
        logger.error(f"Failed to send Telegram message: {str(e)}")
        return False

def offer_key(offer: Dict) -> str:
    """Identity key for deduplication."""
    return "|".join([
        offer.get("publisher", "").lower(),
        offer.get("brand", "").lower(),
        offer.get("price", "").lower(),
    ])


def load_state() -> Optional[Dict[str, List[Dict]]]:
    """Return persisted offers, or None on first run."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except Exception as e:
        logger.warning(f"Could not load state file: {e}")
        return None


def save_state(offers_by_keyword: Dict[str, List[Dict]]) -> None:
    try:
        with open(STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(offers_by_keyword, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.warning(f"Could not save state file: {e}")


def diff_offers(
    current: Dict[str, List[Dict]],
    previous: Dict[str, List[Dict]],
) -> Dict[str, List[Dict]]:
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
        # Validate configuration
        validate_config()

        # Fetch offers for all keywords
        offers_by_keyword = {}
        failed_keywords = []

        # using multithreading to send requests in parallel
        with ThreadPoolExecutor(max_workers=min(10, len(KEYWORDS))) as executor:
            future_to_keyword = {
                executor.submit(fetch_offers, kw.strip()): kw.strip()
                for kw in KEYWORDS if kw.strip()
            }

            for future in as_completed(future_to_keyword):
                keyword = future_to_keyword[future]
                try:
                    offers = future.result()
                    offers_by_keyword[keyword] = offers
                except KaufdaAPIError as e:
                    logger.error(str(e))
                    failed_keywords.append(keyword)

        # Load previous state, save current, compute diff
        prev_state = load_state()
        save_state(offers_by_keyword)

        if prev_state is None:
            logger.info("No previous state found, reporting all current offers")
            to_report = offers_by_keyword
        else:
            to_report = diff_offers(offers_by_keyword, prev_state)

        # Format and send
        if not to_report:
            message = f"✅ No new deals since last check ({datetime.now():%d.%m.%Y})."
            logger.info("No new deals found")
        else:
            message = format_message(to_report) or "No offers found matching your criteria."

        # Output results (for debugging)
        print(message)

        # Send to Telegram if configured
        send_to_telegram(message)

        # Report any failures
        if failed_keywords:
            logger.warning(f"Failed to fetch offers for: {', '.join(failed_keywords)}")

        return 0

    except ConfigurationError as e:
        logger.error(f"Configuration error: {str(e)}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {str(e)}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
