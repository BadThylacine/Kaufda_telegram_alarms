# Kaufda Offer Tracker

Python script that monitors [Kaufda.de](https://www.kaufda.de) for grocery deals matching your keywords and sends notifications via Telegram.

## Features

- Searches for products by keyword
- Filters offers by maximum price
- Uses location-based search
- Sends formatted notifications to Telegram
- Logs network and parsing errors
- Sorts results by supermarket name

## Prerequisites

- Python 3.10+
- `requests`
- Telegram bot token from [@BotFather](https://t.me/botfather)
- Telegram chat ID

## Installation

```bash
pip install requests
```

## Configuration

The script reads these environment variables:

| Variable | Description | Default |
|----------|-------------|---------|
| `TELEGRAM_TOKEN` | Telegram bot token | empty |
| `CHAT_ID` | Telegram chat ID | empty |
| `KEYWORDS` | Comma-separated search keywords | `lachs` |
| `MAX_PRICE` | Maximum price filter | `4.0` |
| `SEARCH_LAT` | Search latitude | `52.4669` |
| `SEARCH_LNG` | Search longitude | `13.4299` |
| `STATE_FILE` | Local state file path | `kaufda_state.json` |
| `DATA_QUERIES` | Comma-separated queries archived in Supabase | Same as `KEYWORDS` |
| `COLLECTION_LIMIT` | Maximum records requested from Kaufda | `100` |
| `SUPABASE_URL` | Existing Supabase project URL | empty |
| `SUPABASE_SERVICE_ROLE_KEY` | Supabase backend service role key | empty |

Example shell configuration:

```bash
export TELEGRAM_TOKEN="your_bot_token_here"
export CHAT_ID="your_chat_id_here"
export KEYWORDS="lachs,cheddar,pastrami"
export MAX_PRICE="5.0"
export SEARCH_LAT="52.4669"
export SEARCH_LNG="13.4299"
export STATE_FILE="kaufda_state.json"
export DATA_QUERIES="lachs,cheddar,pastrami"
export COLLECTION_LIMIT="100"
export SUPABASE_URL="https://your-project.supabase.co"
export SUPABASE_SERVICE_ROLE_KEY="your-service-role-key"
```

## Usage

Run the script directly:

```bash
python kaufda_alerts_version_cloud.py
```

With inline environment variables:

```bash
KEYWORDS="lachs,butter" MAX_PRICE="3.0" python kaufda_alerts_version_cloud.py
```

## Output

The script:

1. Validates configuration
2. Fetches offers for each keyword in parallel
3. Archives complete responses and normalized data in Supabase when configured
4. Filters by price for Telegram
5. Sorts results by publisher
6. Prints the message to console
7. Sends the message to Telegram if credentials are configured

Run `supabase_schema.sql` once in the Supabase SQL editor before enabling ingestion.
Supabase stores every returned offer, including offers above `MAX_PRICE`; that setting
only controls Telegram alerts.
