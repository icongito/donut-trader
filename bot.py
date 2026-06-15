"""
Donut SMP — AH Lowest Sale Price Tracker
Polls /v1/auction/transactions and alerts on Discord whenever a tracked
item sells for a new daily LOW price. At midnight sends a daily summary
showing the lowest price recorded for each item that day.
"""

import json
import logging
import os
import random
import sys
import time
from datetime import datetime, timezone

import requests
from discord_webhook import DiscordEmbed, DiscordWebhook
from dotenv import load_dotenv

load_dotenv()

API_BASE    = "https://api.donutsmp.net"
API_KEY     = os.getenv("API_KEY", "")
DISCORD_URL = os.getenv("DISCORD_WEBHOOK_URL", "")
POLL_MIN    = int(os.getenv("POLL_MIN_SECONDS", "5"))
POLL_MAX    = int(os.getenv("POLL_MAX_SECONDS", "10"))

_WIKI = "https://minecraft.wiki/images"
TRACKED_ITEMS = [
    # (keyword, label, emoji, embed color, thumbnail url)
    ("totem",         "Totem of Undying", "🛡️", "3498DB", os.getenv("THUMB_TOTEM",        f"{_WIKI}/Totem_of_Undying_JE2_BE2.png")),
    ("emerald_block", "Emerald Block",    "💚",  "2ECC71", os.getenv("THUMB_EMERALD_BLOCK", f"{_WIKI}/Block_of_Emerald_JE4_BE3.png?d5a3c")),
    ("gold_block",    "Gold Block",       "🟡",  "F1C40F", os.getenv("THUMB_GOLD_BLOCK",    f"{_WIKI}/Block_of_Gold_JE6_BE3.png")),
]

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("price_tracker.log"),
    ],
)
log = logging.getLogger(__name__)

# Per-item daily lowest price: { keyword -> float | None }
daily_low: dict = {kw: None for kw, _, _, _, _ in TRACKED_ITEMS}
# Keeps the record of that low: { keyword -> (price, time, seller) | None }
daily_low_record: dict = {kw: None for kw, _, _, _, _ in TRACKED_ITEMS}

seen_tx_ids: set = set()
current_day: str = datetime.now(timezone.utc).strftime("%Y-%m-%d")


def _headers() -> dict:
    h = {"Accept": "application/json", "User-Agent": "DonutSMP-PriceBot/1.0"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _tx_id(tx: dict) -> str:
    seller_uuid = tx.get("seller", {}).get("uuid", "")
    ms = tx.get("unixMillisDateSold", 0)
    price = tx.get("price", 0)
    return f"{seller_uuid}:{ms}:{price}"


def _match_item(tx: dict):
    item = tx.get("item", {})
    item_id   = str(item.get("id", "")).lower()
    disp_name = str(item.get("display_name", "")).lower()
    for keyword, label, emoji, color, thumb in TRACKED_ITEMS:
        if keyword in item_id or keyword in disp_name:
            return keyword, label, emoji, color, thumb
    return None


def fetch_transactions(page: int) -> list:
    url = f"{API_BASE}/v1/auction/transactions/{page}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        r.raise_for_status()
        return r.json().get("result", [])
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            log.error("401 Unauthorized — set API_KEY in .env (use /api in-game to generate one)")
        elif e.response.status_code == 500:
            log.warning("Page %d: server error", page)
        else:
            log.error("HTTP %s on page %d", e.response.status_code, page)
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.error("Error fetching page %d: %s", page, e)
    return []


def send_new_low_alert(price, seller, label, sold_at, emoji, color, thumb):
    log.info("NEW DAILY LOW: %s — %.2f coins — %s — %s", label, price, seller, sold_at)
    if not DISCORD_URL:
        return
    webhook = DiscordWebhook(url=DISCORD_URL, username="Donut Price Bot")
    embed = DiscordEmbed(
        title=f"{emoji} New Daily Low: {label}",
        description=(
            f"**{label}** just sold at the cheapest price today!\n\n"
            f"💸 **{price:,.2f} coins**\n"
            f"👤 Seller: `{seller}`\n"
            f"🕐 Sold at: `{sold_at}`"
        ),
        color=color,
    )
    embed.set_thumbnail(url=thumb)
    embed.set_timestamp()
    webhook.add_embed(embed)
    webhook.execute()


def send_daily_summary():
    log.info("Sending daily summary...")
    if not DISCORD_URL:
        return

    date_str = current_day
    webhook = DiscordWebhook(url=DISCORD_URL, username="Donut Price Bot")
    embed = DiscordEmbed(
        title="📅 Daily Price Summary",
        description=f"Lowest sale prices recorded on **{date_str}**",
        color="9B59B6",
    )

    has_data = False
    for keyword, label, emoji, color, thumb in TRACKED_ITEMS:
        record = daily_low_record.get(keyword)
        if record:
            has_data = True
            price, sold_at, seller = record
            embed.add_embed_field(
                name=f"{emoji} {label}",
                value=(
                    f"💸 Lowest: **{price:,.2f} coins**\n"
                    f"🕐 Time: `{sold_at}`\n"
                    f"👤 Seller: `{seller}`"
                ),
                inline=False,
            )

    if not has_data:
        embed.add_embed_field(name="No data", value="No tracked sales recorded today.", inline=False)

    embed.set_timestamp()
    webhook.add_embed(embed)
    webhook.execute()


def reset_daily():
    global current_day
    for kw in daily_low:
        daily_low[kw] = None
        daily_low_record[kw] = None
    current_day = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    log.info("Daily stats reset for %s.", current_day)


def check_day_rollover():
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    if today != current_day:
        send_daily_summary()
        reset_daily()


def poll():
    check_day_rollover()

    new_txs = []

    for page in range(1, 11):
        txs = fetch_transactions(page)
        if not txs:
            break

        found_new = False
        for tx in txs:
            tid = _tx_id(tx)
            if tid not in seen_tx_ids:
                found_new = True
                seen_tx_ids.add(tid)
                match = _match_item(tx)
                if match:
                    new_txs.append((tx, *match))

        if not found_new:
            break

        time.sleep(0.2)

    if not new_txs:
        log.info("Poll complete — no new tracked sales.")
        return

    log.info("Found %d new tracked transaction(s).", len(new_txs))

    for tx, keyword, label, emoji, color, thumb in new_txs:
        price   = float(tx.get("price", 0))
        seller  = tx.get("seller", {}).get("name", "unknown")
        ms_sold = tx.get("unixMillisDateSold", 0)
        sold_at = (
            datetime.fromtimestamp(ms_sold / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if ms_sold else "unknown"
        )

        log.info("%s sale: %.2f coins — %s — %s", label, price, seller, sold_at)

        current_low = daily_low[keyword]
        if current_low is None or price < current_low:
            daily_low[keyword] = price
            daily_low_record[keyword] = (price, sold_at, seller)
            send_new_low_alert(price, seller, label, sold_at, emoji, color, thumb)


def main():
    if not API_KEY:
        log.warning("API_KEY not set — requests will return 401. Use /api in-game.")
    if not DISCORD_URL:
        log.warning("DISCORD_WEBHOOK_URL not set — alerts will only appear in the log.")

    log.info("Price tracker started. Tracking: %s", ", ".join(l for _, l, _, _, _ in TRACKED_ITEMS))
    log.info("Polling every %d-%ds.", POLL_MIN, POLL_MAX)
    log.info("Daily summary sent automatically at midnight UTC.")

    while True:
        try:
            poll()
        except Exception as e:
            log.exception("Unexpected error during poll: %s", e)
        wait = random.randint(POLL_MIN, POLL_MAX)
        log.debug("Next poll in %ds.", wait)
        time.sleep(wait)


if __name__ == "__main__":
    main()
