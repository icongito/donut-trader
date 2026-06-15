"""
Donut SMP — AH Highest Sale Price Tracker
Polls /v1/auction/transactions and alerts on Discord whenever a tracked
item sells for a new all-time high price.
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

# Each entry: (keyword to match in item id/display_name, label, discord emoji)
_MC = "https://raw.githubusercontent.com/InventivetalentDev/minecraft-assets/1.20.4/assets/minecraft/textures"
TRACKED_ITEMS = [
    # (keyword, label, emoji, embed color, thumbnail url)
    ("totem",         "Totem of Undying", "🛡️", "3498DB", f"{_MC}/item/totem_of_undying.png"),
    ("emerald_block", "Emerald Block",    "💚",  "2ECC71", f"{_MC}/block/emerald_block.png"),
    ("gold_block",    "Gold Block",       "🟡",  "F1C40F", f"{_MC}/block/gold_block.png"),
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

# Per-item highest price: { keyword -> float }
highest: dict = {kw: 0.0 for kw, _, _, _, _ in TRACKED_ITEMS}
seen_tx_ids: set[str] = set()


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
    """Return (keyword, label, emoji, color, thumb) if this tx matches a tracked item, else None."""
    item = tx.get("item", {})
    item_id   = str(item.get("id", "")).lower()
    disp_name = str(item.get("display_name", "")).lower()
    for keyword, label, emoji, color, thumb in TRACKED_ITEMS:
        if keyword in item_id or keyword in disp_name:
            return keyword, label, emoji, color, thumb
    return None


def fetch_transactions(page: int) -> list[dict]:
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


def send_alert(price: float, seller: str, item_name: str, sold_at: str, emoji: str, color: str, thumb: str):
    log.info("NEW HIGH: %s — %.2f coins — %s — %s", item_name, price, seller, sold_at)
    if not DISCORD_URL:
        return
    webhook = DiscordWebhook(url=DISCORD_URL, username="Donut Price Bot")
    embed = DiscordEmbed(
        title=f"{emoji} New Highest Sale: {item_name}",
        description=(
            f"**{item_name}** just sold for a new record price!\n\n"
            f"💰 **{price:,.2f} coins**\n"
            f"👤 Seller: `{seller}`\n"
            f"🕐 Sold at: `{sold_at}`"
        ),
        color=color,
    )
    embed.set_thumbnail(url=thumb)
    embed.set_timestamp()
    webhook.add_embed(embed)
    webhook.execute()


def poll():
    new_txs = []  # (tx, keyword, label, emoji)

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

        if price > highest[keyword]:
            highest[keyword] = price
            send_alert(price, seller, label, sold_at, emoji, color, thumb)


def main():
    if not API_KEY:
        log.warning("API_KEY not set — requests will return 401. Use /api in-game.")
    if not DISCORD_URL:
        log.warning("DISCORD_WEBHOOK_URL not set — alerts will only appear in the log.")

    log.info("Price tracker started. Tracking: %s", ", ".join(l for _, l, _, _, _ in TRACKED_ITEMS))
    log.info("Polling every %d-%ds.", POLL_MIN, POLL_MAX)

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
