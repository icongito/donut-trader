"""
Donut SMP — Totem Highest Sale Price Tracker
Polls /v1/auction/transactions and alerts on Discord whenever a totem
sells for a new all-time high price.
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

API_BASE        = "https://api.donutsmp.net"
API_KEY         = os.getenv("API_KEY", "")          # /api in-game to generate
DISCORD_URL     = os.getenv("DISCORD_WEBHOOK_URL", "")
POLL_MIN        = int(os.getenv("POLL_MIN_SECONDS", "5"))   # minimum poll interval
POLL_MAX        = int(os.getenv("POLL_MAX_SECONDS", "10"))  # maximum poll interval

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("totem_tracker.log"),
    ],
)
log = logging.getLogger(__name__)

# Running state
highest_price: float = 0.0
seen_tx_ids: set[str] = set()   # deduplicate by (seller_uuid + price + ms_sold)


def _headers() -> dict:
    h = {"Accept": "application/json", "User-Agent": "DonutSMP-TotemBot/1.0"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h


def _tx_id(tx: dict) -> str:
    """Stable deduplcation key for a transaction."""
    seller_uuid = tx.get("seller", {}).get("uuid", "")
    ms = tx.get("unixMillisDateSold", 0)
    price = tx.get("price", 0)
    return f"{seller_uuid}:{ms}:{price}"


def _is_totem(tx: dict) -> bool:
    item = tx.get("item", {})
    item_id   = str(item.get("id", "")).lower()
    disp_name = str(item.get("display_name", "")).lower()
    return "totem" in item_id or "totem" in disp_name


def fetch_transactions(page: int) -> list[dict]:
    url = f"{API_BASE}/v1/auction/transactions/{page}"
    try:
        r = requests.get(url, headers=_headers(), timeout=15)
        r.raise_for_status()
        data = r.json()
        return data.get("result", [])
    except requests.HTTPError as e:
        if e.response.status_code == 401:
            log.error("401 Unauthorized — set API_KEY in .env (use /api in-game to generate one)")
        elif e.response.status_code == 500:
            log.warning("Page %d: server error (page may not exist)", page)
        else:
            log.error("HTTP %s on page %d", e.response.status_code, page)
    except (requests.RequestException, json.JSONDecodeError) as e:
        log.error("Error fetching page %d: %s", page, e)
    return []


def send_alert(price: float, seller: str, item_name: str, sold_at: str):
    log.info("NEW HIGH: %s sold by %s for %.2f at %s", item_name, seller, price, sold_at)
    if not DISCORD_URL:
        return
    webhook = DiscordWebhook(url=DISCORD_URL, username="Totem Price Bot")
    embed = DiscordEmbed(
        title="🏆 New Highest Totem Sale!",
        description=(
            f"**{item_name}** just sold for a new record price!\n\n"
            f"💰 **{price:,.2f} coins**\n"
            f"👤 Seller: `{seller}`\n"
            f"🕐 Sold at: `{sold_at}`"
        ),
        color="FFD700",
    )
    embed.set_timestamp()
    webhook.add_embed(embed)
    webhook.execute()


def poll():
    global highest_price

    new_txs = []

    # Transactions are ordered newest-first; page 1 is most recent.
    # We only need to scan until we hit already-seen entries.
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
                if _is_totem(tx):
                    new_txs.append(tx)

        # If every entry on this page was already seen, stop paginating
        if not found_new:
            break

        time.sleep(0.2)   # be polite

    if not new_txs:
        log.info("Poll complete — no new totem sales.")
        return

    log.info("Found %d new totem transaction(s).", len(new_txs))

    for tx in new_txs:
        price     = float(tx.get("price", 0))
        seller    = tx.get("seller", {}).get("name", "unknown")
        item_name = tx["item"].get("display_name") or tx["item"].get("id", "Totem of Undying")
        ms_sold   = tx.get("unixMillisDateSold", 0)
        sold_at   = (
            datetime.fromtimestamp(ms_sold / 1000, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
            if ms_sold else "unknown"
        )

        log.info("Totem sale: %.2f coins — %s — %s", price, seller, sold_at)

        if price > highest_price:
            highest_price = price
            send_alert(price, seller, item_name, sold_at)


def main():
    if not API_KEY:
        log.warning("API_KEY is not set. Requests will likely return 401. Use /api in-game to generate a key.")
    if not DISCORD_URL:
        log.warning("DISCORD_WEBHOOK_URL is not set. Alerts will only appear in the log.")

    log.info("Totem tracker started. Polling every %d-%ds.", POLL_MIN, POLL_MAX)
    log.info("Current highest known price: %.2f", highest_price)

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
