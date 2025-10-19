import os
import sys
import logging
import requests
import re
from datetime import datetime
from typing import List, Dict, Optional

# --- CONFIGURATION ---
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# Configuration with environment variable fallback
TELEGRAM_TOKEN = os.getenv("TELEGRAM_TOKEN", "")
CHAT_ID = os.getenv("CHAT_ID", "")
KEYWORDS = os.getenv("KEYWORDS", "lachs,cheddar,parmesan").split(",")
MAX_PRICE = float(os.getenv("MAX_PRICE", "5.0"))
SEARCH_LAT = float(os.getenv("SEARCH_LAT", "52.4669"))
SEARCH_LNG = float(os.getenv("SEARCH_LNG", "13.4299"))
SEARCH_SIZE = int(os.getenv("SEARCH_SIZE", "25"))
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
        return float(price_value)

    if isinstance(price_value, str):
        price_match = re.search(r"\d+[.,]\d+", price_value)
        if price_match:
            try:
                return float(price_match.group(0).replace(",", "."))
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
    url = "https://www.kaufda.de/webapp/api/slots/offerSearch"
    params = {
        "searchQuery": keyword,
        "lat": SEARCH_LAT,
        "lng": SEARCH_LNG,
        "size": SEARCH_SIZE
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
    contents = data.get("_embedded", {}).get("contents", [])

    if not contents:
        logger.info(f"No results found for keyword: {keyword}")
        return []

    for item in contents:
        try:
            c = item["content"]

            # Extract and validate price
            price_raw = c.get("deals", [{}])[0].get("min")
            price = parse_price(price_raw)

            if price is None:
                logger.debug(f"Skipping item with invalid price: {price_raw}")
                continue

            if price > MAX_PRICE:
                logger.debug(f"Skipping item over max price: {price}€")
                continue

            # Extract product info
            product = c.get("products", [{}])[0]
            profile = c.get("publicationProfiles", [{}])[0]
            validity = profile.get("validity", {})

            # Parse end date
            end_date_str = validity.get("endDate")
            if not end_date_str:
                logger.warning("Missing end date, skipping item")
                continue

            try:
                end_date = datetime.strptime(
                    end_date_str,
                    "%Y-%m-%dT%H:%M:%S.%f%z"
                ).strftime("%d.%m.%Y")
            except ValueError as e:
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
                f"🛒 <b>{o['publisher']}</b> — {o['brand']} {o['name']}: "
                f"{o['price']} (until {o['endDate']}) {emoji}".strip()
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


def main() -> int:
    try:
        # Validate configuration
        validate_config()

        # Fetch offers for all keywords
        offers_by_keyword = {}
        failed_keywords = []

        for keyword in KEYWORDS:
            keyword = keyword.strip()
            if not keyword:
                continue

            try:
                offers = fetch_offers(keyword)
                offers_by_keyword[keyword] = offers
            except KaufdaAPIError as e:
                logger.error(str(e))
                failed_keywords.append(keyword)
                continue

        # Format message
        message = format_message(offers_by_keyword)

        if not message:
            logger.warning("No offers found for any keyword")
            print("No offers found.")
            return 0

        # Output results
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