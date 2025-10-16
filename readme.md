# Kaufda Offer Tracker

A Python script that monitors [Kaufda.de](https://www.kaufda.de) for new grocery deals matching your keywords and sends notifications via Telegram.

## Features

- Searches for specific products (cheese, salmon, pastrami, etc.)
- Tracks new offers by comparing against previous results
- Sends formatted notifications to Telegram
- Stores results locally to avoid duplicate alerts

## Setup

1. **Install dependencies:**
   ```bash
   pip install requests
   ```

2. **Configure the script:**
   - Replace `TELEGRAM_TOKEN` with your bot token from [@BotFather](https://t.me/botfather)
   - Replace `CHAT_ID` with your Telegram chat ID
   - Adjust `KEYWORDS` list with products you want to track
   - Update `LAT` and `LNG` if you're not in Berlin

3. **Run the script:**
   ```bash
   python script.py
   ```

## Configuration

```python
TELEGRAM_TOKEN = "your_telegram_bot_token"  # Get from @BotFather
CHAT_ID = "your_chat_id"                    # Your Telegram chat ID
KEYWORDS = ["cheddar", "lachs", "pastrami"] # Products to search
LAT, LNG = 52.4669, 13.4299                 # Location coordinates
```

## How It Works

1. Fetches current offers from Kaufda API for each keyword
2. Compares with previously saved results (`last_results.json`)
3. Sends Telegram notification for new offers only
4. Updates the saved results for next run

## Automation

Schedule with cron (Linux/Mac) or Task Scheduler (Windows) to run daily:

```bash
# Run every day at 9 AM
0 9 * * * /usr/bin/python3 /path/to/script.py
```

## Note

The Telegram notification is currently commented out. Remove the `#` before `send_to_telegram(message)` to enable notifications.