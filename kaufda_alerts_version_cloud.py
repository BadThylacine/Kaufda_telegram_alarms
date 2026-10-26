import json
import hashlib
import logging
import os
import re
import sys
import uuid
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
DATA_QUERIES = [k.strip() for k in os.getenv("DATA_QUERIES", ",".join(KEYWORDS)).split(",") if k.strip()]
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
COLLECTION_LIMIT = int(os.getenv("COLLECTION_LIMIT", "100"))
SUPABASE_URL = os.getenv("SUPABASE_URL", "").rstrip("/")
SUPABASE_SERVICE_ROLE_KEY = os.getenv("SUPABASE_SERVICE_ROLE_KEY", "")

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


class SupabaseError(Exception):
    """Raised when Supabase ingestion cannot be completed."""


def validate_config() -> None:
    errors = []
    if not KEYWORDS:
        errors.append("KEYWORDS list is empty")
    if not DATA_QUERIES:
        errors.append("DATA_QUERIES list is empty")
    if MAX_PRICE <= 0:
        errors.append(f"MAX_PRICE must be positive, got {MAX_PRICE}")
    if TELEGRAM_TOKEN and not CHAT_ID:
        errors.append("CHAT_ID required when TELEGRAM_TOKEN is set")

    if errors:
        raise ConfigurationError(f"Configuration errors: {', '.join(errors)}")

    if SUPABASE_URL and not SUPABASE_SERVICE_ROLE_KEY:
        errors.append("SUPABASE_SERVICE_ROLE_KEY required when SUPABASE_URL is set")
    if SUPABASE_SERVICE_ROLE_KEY and not SUPABASE_URL:
        errors.append("SUPABASE_URL required when SUPABASE_SERVICE_ROLE_KEY is set")
    if COLLECTION_LIMIT <= 0:
        errors.append(f"COLLECTION_LIMIT must be positive, got {COLLECTION_LIMIT}")

    if errors:
        raise ConfigurationError(f"Configuration errors: {', '.join(errors)}")

    logger.info("Configuration valid - Keywords: %s, Data queries: %s, Max price: %s EUR",
                KEYWORDS, DATA_QUERIES, MAX_PRICE)


# -----------------------------------------------------------------------------
# API & Data Helpers
# -----------------------------------------------------------------------------

def parse_price(price_value: Any) -> Optional[float]:
    if isinstance(price_value, (int, float)):
        return round(float(price_value), 2)
    if isinstance(price_value, str) and (match := re.search(r"\d+[.,]\d+", price_value)):
        return round(float(match.group(0).replace(",", ".")), 2)
    return None


def fetch_search(keyword: str) -> Tuple[Dict[str, Any], bool, Optional[int]]:
    """Fetch the complete Kaufda response for a single query."""
    params = {
        "query": keyword,
        "lat": SEARCH_LAT,
        "lng": SEARCH_LNG,
        "limit": COLLECTION_LIMIT,
    }
    try:
        resp = session.get(KAUFDA_SEARCH_URL, params=params, timeout=REQUEST_TIMEOUT)
        resp.raise_for_status()
        payload = resp.json()
        if not isinstance(payload, dict):
            raise ValueError("response root is not an object")
        return payload, True, resp.status_code
    except requests.exceptions.Timeout:
        logger.error("Request timeout for query '%s'", keyword)
    except requests.exceptions.RequestException as exc:
        status = exc.response.status_code if exc.response is not None else None
        logger.error("API request failed for '%s': %s", keyword, exc)
        return {}, False, status
    except ValueError as exc:
        logger.error("Invalid JSON response for '%s': %s", keyword, exc)
    return {}, False, None


def offers_for_alert(payload: Dict[str, Any], keyword: str) -> List[Dict[str, Any]]:
    """Extract the existing, price-filtered alert representation from a response.

    Storage receives every offer. Telegram continues to receive only offers
    matching MAX_PRICE, preserving the original application behavior.
    """
    contents = payload.get("searchResults", {}).get("contents", {}).get("offers", [])
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

    logger.info("Found %d alert offers for '%s'", len(results), keyword)
    return sorted(results, key=lambda x: x["publisher"].lower())


def all_offers(payload: Dict[str, Any]) -> List[Dict[str, Any]]:
    return payload.get("searchResults", {}).get("contents", {}).get("offers", [])


def response_contents(payload: Dict[str, Any]) -> Dict[str, Any]:
    return payload.get("searchResults", {}).get("contents", {})


def _publisher_value(publisher: Any, key: str) -> Optional[str]:
    if isinstance(publisher, dict):
        value = publisher.get(key)
        return str(value) if value is not None else None
    return None


def _json_hash(payload: Dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class SupabaseClient:
    def __init__(self, base_url: str, service_role_key: str):
        self.base_url = f"{base_url}/rest/v1"
        self.headers = {
            "apikey": service_role_key,
            "Authorization": f"Bearer {service_role_key}",
            "Content-Type": "application/json",
        }

    def upsert(self, table: str, rows: List[Dict[str, Any]], conflict_columns: str) -> None:
        if not rows:
            return
        response = session.post(
            f"{self.base_url}/{table}",
            params={"on_conflict": conflict_columns},
            headers={**self.headers, "Prefer": "resolution=merge-duplicates,return=minimal"},
            json=rows,
            timeout=REQUEST_TIMEOUT,
        )
        if not response.ok:
            raise SupabaseError(f"{table} upsert failed ({response.status_code}): {response.text[:500]}")

    def ingest(self, query: str, payload: Dict[str, Any], success: bool, http_status: Optional[int]) -> None:
        run_id = str(uuid.uuid4())
        contents = response_contents(payload)
        brochures = contents.get("brochures", []) if isinstance(contents, dict) else []
        offers = contents.get("offers", []) if isinstance(contents, dict) else []
        metadata = payload.get("searchResults", {}).get("metadata", {})
        counts = metadata.get("contentCount", {}) if isinstance(metadata, dict) else {}
        expected_brochures = counts.get("brochure")
        expected_offers = counts.get("offer")
        counts_match = (
            success
            and expected_brochures is not None
            and expected_offers is not None
            and len(brochures) == expected_brochures
            and len(offers) == expected_offers
        )

        run = {
            "id": run_id,
            "query": query,
            "search_lat": SEARCH_LAT,
            "search_lng": SEARCH_LNG,
            "request_params": {
                "query": query,
                "lat": SEARCH_LAT,
                "lng": SEARCH_LNG,
                "limit": COLLECTION_LIMIT,
            },
            "status": "success" if success else "failed",
            "http_status": http_status,
            "returned_brochures": len(brochures),
            "expected_brochures": expected_brochures,
            "returned_offers": len(offers),
            "expected_offers": expected_offers,
            "counts_match": counts_match,
        }
        self.upsert("collection_runs", [run], "id")

        if not success:
            return

        self.upsert("raw_responses", [{
            "run_id": run_id,
            "payload": payload,
            "response_sha256": _json_hash(payload),
        }], "run_id")

        brochure_rows: Dict[str, Dict[str, Any]] = {}
        page_rows: Dict[Tuple[str, int], Dict[str, Any]] = {}
        for wrapper in brochures:
            content = wrapper.get("content", {}) if isinstance(wrapper, dict) else {}
            if not isinstance(content, dict):
                continue
            content_id = content.get("contentId") or content.get("id")
            if not content_id:
                continue
            content_id = str(content_id)
            publisher = content.get("publisher", {})
            brochure_rows[content_id] = {
                "content_id": content_id,
                "legacy_id": str(content["legacyId"]) if content.get("legacyId") is not None else None,
                "publisher_id": _publisher_value(publisher, "id"),
                "publisher_name": _publisher_value(publisher, "name") or content.get("title"),
                "title": content.get("title"),
                "published_from": content.get("publishedFrom"),
                "published_until": content.get("publishedUntil"),
                "valid_from": content.get("validFrom"),
                "valid_until": content.get("validUntil"),
                "page_count": content.get("pageCount"),
                "last_seen_at": datetime.utcnow().isoformat() + "Z",
                "raw": content,
            }
            page_number = content.get("pageNumber", content.get("page"))
            if page_number is not None:
                page_key = (content_id, int(page_number))
                page_rows[page_key] = {
                    "content_id": content_id,
                    "page_number": int(page_number),
                    "image_url": content.get("image"),
                    "raw": content,
                }

        self.upsert("brochures", list(brochure_rows.values()), "content_id")
        self.upsert("brochure_pages", list(page_rows.values()), "content_id,page_number")

        offer_rows: Dict[str, Dict[str, Any]] = {}
        observation_rows: Dict[Tuple[str, str, str], Dict[str, Any]] = {}
        for offer in offers:
            if not isinstance(offer, dict) or not offer.get("id"):
                continue
            offer_id = str(offer["id"])
            prices = offer.get("prices", {}) if isinstance(offer.get("prices"), dict) else {}
            parent = offer.get("parentContent", {}) if isinstance(offer.get("parentContent"), dict) else {}
            page = parent.get("page", {}) if isinstance(parent.get("page"), dict) else {}
            content_id = parent.get("id")
            offer_rows[offer_id] = {
                "offer_id": offer_id,
                "publisher_id": offer.get("publisherId"),
                "publisher_name": offer.get("publisherName"),
                "title": offer.get("title"),
                "categories": offer.get("categories", []),
                "last_seen_at": datetime.utcnow().isoformat() + "Z",
                "raw": offer,
            }
            observation_key = (run_id, offer_id, query)
            observation_rows[observation_key] = {
                "run_id": run_id,
                "offer_id": offer_id,
                "content_id": str(content_id) if content_id is not None else None,
                "page_number": page.get("pageNumber", page.get("page")),
                "query": query,
                "main_price": prices.get("mainPrice"),
                "secondary_price": prices.get("secondaryPrice"),
                "main_price_formatted": prices.get("mainPriceFormatted"),
                "secondary_price_formatted": prices.get("secondaryPriceFormatted"),
                "price_by_base_unit": prices.get("priceByBaseUnit"),
                "price_conditions": prices.get("conditions", []),
                "raw": offer,
            }

        self.upsert("offers", list(offer_rows.values()), "offer_id")
        self.upsert("offer_observations", list(observation_rows.values()), "run_id,offer_id,query")
        logger.info("Stored query '%s': %d brochures, %d offers, counts_match=%s",
                    query, len(brochure_rows), len(offer_rows), counts_match)


def ingest_results(results: List[Tuple[str, Dict[str, Any], bool, Optional[int]]]) -> None:
    if not SUPABASE_URL:
        logger.info("Supabase credentials not configured, skipping data ingestion")
        return
    client = SupabaseClient(SUPABASE_URL, SUPABASE_SERVICE_ROLE_KEY)
    for query, payload, success, http_status in results:
        try:
            client.ingest(query, payload, success, http_status)
        except (requests.exceptions.RequestException, SupabaseError, TypeError, ValueError) as exc:
            logger.error("Supabase ingestion failed for '%s': %s", query, exc)


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
        all_queries = list(dict.fromkeys([*DATA_QUERIES, *KEYWORDS]))
        fetched: Dict[str, Tuple[Dict[str, Any], bool, Optional[int]]] = {}
        ingestion_results: List[Tuple[str, Dict[str, Any], bool, Optional[int]]] = []

        with ThreadPoolExecutor(max_workers=min(10, len(all_queries))) as executor:
            for query, result in zip(all_queries, executor.map(fetch_search, all_queries)):
                payload, success, http_status = result
                fetched[query] = result
                ingestion_results.append((query, payload, success, http_status))
                if query in KEYWORDS and success:
                    offers = offers_for_alert(payload, query)
                    offers_by_kw[query] = offers
                elif query in KEYWORDS:
                    failed_keywords.append(query)

        ingest_results(ingestion_results)

        for keyword in KEYWORDS:
            if keyword not in fetched:
                failed_keywords.append(keyword)
            elif fetched[keyword][1]:
                offers_by_kw.setdefault(keyword, offers_for_alert(fetched[keyword][0], keyword))

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
            logger.warning("Failed to fetch offers for: %s", ", ".join(sorted(set(failed_keywords))))

        return 0

    except ConfigurationError as exc:
        logger.error("Configuration error: %s", exc)
        return 1
    except Exception as exc:
        logger.exception("Unexpected error: %s", exc)
        return 1


if __name__ == "__main__":
    sys.exit(main())  # active entrypoint
if False:
            msg = f"✅ No new deals since last check ({datetime.now():%d.%m.%Y})."
