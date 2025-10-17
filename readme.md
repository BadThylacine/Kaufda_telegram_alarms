# Kaufda Offer Tracker

A production-ready Python script that monitors [Kaufda.de](https://www.kaufda.de) for grocery deals matching your keywords and sends notifications via Telegram.

## Features

- 🔍 Searches for specific products (salmon, cheese, etc.)
- 💰 Filters offers by maximum price
- 📍 Location-based search (default: Berlin)
- 📱 Sends formatted notifications to Telegram
- ✅ Production-ready with error handling and logging
- 🏪 Alphabetically sorted by supermarket
- 💥 Highlights specific stores (e.g., REWE)

## Prerequisites

- Python 3.7+
- Telegram Bot Token (get from [@BotFather](https://t.me/botfather))
- Your Telegram Chat ID

## Installation

1. **Clone or download the script**

2. **Install dependencies:**
   ```bash
   pip install requests
   ```

## Configuration

### Option 1: Environment Variables (Recommended)

Create a `.env` file or set system environment variables:

```bash
export TELEGRAM_TOKEN="your_bot_token_here"
export CHAT_ID="your_chat_id_here"
export KEYWORDS="lachs,cheddar,pastrami"
export MAX_PRICE="5.0"
export SEARCH_LAT="52.4669"
export SEARCH_LNG="13.4299"
export SEARCH_SIZE="25"
```

### Option 2: Edit Script Directly

Modify the configuration section in the script:

```python
TELEGRAM_TOKEN = "your_bot_token_here"
CHAT_ID = "your_chat_id_here"
KEYWORDS = ["lachs", "cheddar", "pastrami"]
MAX_PRICE = 5.0
SEARCH_LAT = 52.4669  # Berlin latitude
SEARCH_LNG = 13.4299  # Berlin longitude
```

### Configuration Options

| Variable | Description | Default |
|----------|-------------|---------|
| `TELEGRAM_TOKEN` | Your Telegram bot token | Required for notifications |
| `CHAT_ID` | Your Telegram chat ID | Required for notifications |
| `KEYWORDS` | Comma-separated product keywords | `"lachs"` |
| `MAX_PRICE` | Maximum price filter (€) | `5.0` |
| `SEARCH_LAT` | Search location latitude | `52.4669` (Berlin) |
| `SEARCH_LNG` | Search location longitude | `13.4299` (Berlin) |
| `SEARCH_SIZE` | Max results per keyword | `25` |

## Usage

### Basic Usage

```bash
python kaufda_bot.py
```

### With Environment Variables

```bash
KEYWORDS="lachs,butter" MAX_PRICE="3.0" python kaufda_bot.py
```

### Using .env file (requires python-dotenv)

```bash
pip install python-dotenv
```

Add to top of script:
```python
from dotenv import load_dotenv
load_dotenv()
```

## Output

The script will:
1. ✅ Validate configuration
2. 🔍 Search for each keyword
3. 📊 Filter by price and location
4. 🏪 Sort results alphabetically by supermarket
5. 📱 Send to Telegram (if configured)
6. 📄 Print results to console

### Example Output

```
🗓 Kaufda Offers (17.10.2025)

🔎 Lachs
🛒 Aldi — FairSea Atlantic Salmon: 4.99€ (until 23.10.2025)
🛒 Edeka — Gut&Günstig Räucherlachs: 3.99€ (until 25.10.2025)
🛒 REWE — REWE Beste Wahl Lachs: 4.49€ (until 24.10.2025) 💥
```

## Automation

### Linux/Mac (Cron)

Run daily at 9 AM:
```bash
crontab -e
```

Add:
```bash
0 9 * * * cd /path/to/script && /usr/bin/python3 kaufda_bot.py >> /var/log/kaufda.log 2>&1
```

### Windows (Task Scheduler)

1. Open Task Scheduler
2. Create Basic Task
3. Set trigger (e.g., Daily at 9:00 AM)
4. Action: Start a program
5. Program: `python`
6. Arguments: `C:\path\to\kaufda_bot.py`

### Docker

```dockerfile
FROM python:3.9-slim
WORKDIR /app
COPY kaufda_bot.py requirements.txt ./
RUN pip install -r requirements.txt
CMD ["python", "kaufda_bot.py"]
```

```bash
docker build -t kaufda-bot .
docker run --env-file .env kaufda-bot
```

## Error Handling

The script includes comprehensive error handling:

- ✅ **Network errors** - Retries and timeouts
- ✅ **Invalid data** - Skips malformed items
- ✅ **API errors** - Logs and continues
- ✅ **Configuration errors** - Validates before running
- ✅ **Missing fields** - Graceful fallbacks

## Logging

View detailed execution logs:

```bash
# Run with debug logging
python kaufda_bot.py 2>&1 | tee kaufda.log
```

Log levels:
- `INFO` - Normal operations
- `WARNING` - Non-critical issues
- `ERROR` - Critical failures

## Troubleshooting

### No offers found
- Check if keywords are spelled correctly
- Adjust `MAX_PRICE` if too restrictive
- Verify location coordinates

### Telegram not sending
- Confirm `TELEGRAM_TOKEN` and `CHAT_ID` are set
- Test bot with `/start` command
- Check bot permissions

### API errors
- Check internet connection
- Verify Kaufda.de is accessible
- Check if API endpoint changed

## Contributing

Feel free to:
- Report bugs
- Suggest features
- Submit pull requests

## License

MIT License - feel free to use and modify

## Disclaimer

This script is for personal use only. Respect Kaufda.de's terms of service and rate limits.