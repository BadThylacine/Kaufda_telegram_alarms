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

- Python 3.7+
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

Example shell configuration:

```bash
export TELEGRAM_TOKEN="your_bot_token_here"
export CHAT_ID="your_chat_id_here"
export KEYWORDS="lachs,cheddar,pastrami"
export MAX_PRICE="5.0"
export SEARCH_LAT="52.4669"
export SEARCH_LNG="13.4299"
export STATE_FILE="kaufda_state.json"
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
3. Filters by price
4. Sorts results by publisher
5. Prints the message to console
6. Sends the message to Telegram if credentials are configured