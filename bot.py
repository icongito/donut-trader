"""
Donut SMP Auction Price Tracker
Polls the auction API, tracks daily high/low prices for configured items,
logs everything to CSV, and sends Discord summaries.
"""

import csv
import json
import logging
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Optional

import requests
import schedule
from discord_webhook import DiscordEmbed, DiscordWebhook
from dotenv import load_dotenv

load_dotenv()

# ── Config ─────────────────────────────────────────────────────────────────────

API_BASE_URL          = os.getenv("API_BASE_URL", "https://api.donutsmp.net/v1/auction/list")
API_KEY               = os.getenv("API_KEY", "")
POLL_INTERVAL         = int(os.getenv("POLL_INTERVAL_MINUTES", "5"))
DISCORD_WEBHOOK_URL   = os.getenv("DISCORD_WEBHOOK_URL", "")
DAILY_REPORT_HOUR     = int(os.getenv("DAILY_REPORT_HOUR", "23"))
LOG_FILE              = os.getenv("LOG_FILE", "price_log.csv")

TRACKED_ITEMS = [
    s.strip().lower()
    for s in os.getenv("TRACKED_ITEMS", "totem,ender_pearl,shulker,end_crystal,shulker_shell").split(",")
    if s.strip()
]

FIELD_ITEM_NAME          = os.getenv("FIELD_ITEM_NAME", "item")
FIELD_PRICE              = os.getenv("FIELD_PRICE", "price")
FIELD_AMOUNT             = os.getenv("FIELD_AMOUNT", "amount")
FIELD_SELLER             = os.getenv("FIELD_SELLER", "seller")
FIELD_TIMESTAMP          = os.getenv("FIELD_TIMESTAMP", "timestamp")
PRICE_IS_TOTAL           = os.getenv("PRICE_IS_TOTAL", "false").lower() == "true"
RESPONSE_LIST_PATH       = os.getenv("RESPONSE_LIST_PATH", "data")
RESPONSE_TOTAL_PAGES_PATH = os.getenv("RESPONSE_TOTAL_PAGES_PATH", "total_pages")

# ── Logging setup ──────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log"),
    ],
)
log = logging.getLogger(__name__)

# ── CSV log ────────────────────────────────────────────────────────────────────

CSV_HEADERS = ["timestamp", "item", "unit_price", "amount", "seller", "poll_time"]

def ensure_csv():
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(CSV_HEADERS)

def append_csv(rows: list[dict]):
    with open(LOG_FILE, "a", newline="") as f:
        w = csv.DictWriter(f, fieldnames=CSV_HEADERS)
        w.writerows(rows)

# ── In-memory daily stats ──────────────────────────────────────────────────────

# { item_key: { "min": (price, time, seller), "max": (price, time, seller), "samples": int } }
daily_stats: dict[str, dict] = defaultdict(lambda: {
    "min": None,
    "max": None,
    "samples": 0,
})

def reset_daily_stats():
    daily_stats.clear()
    log.info("Daily stats reset.")

# ── Helpers ────────────────────────────────────────────────────────────────────

def _dig(obj: Any, path: str) -> Any:
    """Traverse a dot-separated path in a nested dict/list."""
    if not path:
        return obj
    for key in path.split("."):
        if isinstance(obj, dict):
            obj = obj.get(key)
        elif isinstance(obj, list) and key.isdigit():
            obj = obj[int(key)]
        else:
            return None
        if obj is None:
            return None
    return obj


def _parse_timestamp(raw) -> str:
    """Return a human-readable UTC timestamp string."""
    if raw is None:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    try:
        # Unix epoch (int or float)
        ts = float(raw)
        return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    except (ValueError, TypeError):
        pass
    try:
        # ISO-8601 string
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        return dt.strftime("%Y-%m-%d %H:%M:%S UTC")
    except ValueError:
        return str(raw)


def _matches_tracked(item_name: str) -> Optional[str]:
    """Return the matching tracked-item key, or None."""
    lower = item_name.lower()
    for keyword in TRACKED_ITEMS:
        if keyword in lower:
            return keyword
    return None


def _build_headers() -> dict:
    h = {"Accept": "application/json", "User-Agent": "DonutSMP-PriceBot/1.0"}
    if API_KEY:
        h["Authorization"] = f"Bearer {API_KEY}"
    return h

# ── API fetching ───────────────────────────────────────────────────────────────

def fetch_page(page: int) -> Optional[dict]:
    url = f"{API_BASE_URL}/{page}"
    try:
        resp = requests.get(url, headers=_build_headers(), timeout=15)
        resp.raise_for_status()
        return resp.json()
    except requests.HTTPError as e:
        log.error("HTTP %s fetching page %d: %s", e.response.status_code, page, e)
    except requests.RequestException as e:
        log.error("Network error fetching page %d: %s", page, e)
    except json.JSONDecodeError as e:
        log.error("JSON parse error on page %d: %s", page, e)
    return None


def fetch_all_auctions() -> list[dict]:
    auctions = []
    page = 1
    while True:
        data = fetch_page(page)
        if data is None:
            break

        listing = _dig(data, RESPONSE_LIST_PATH)
        if not isinstance(listing, list):
            log.warning("Expected list at '%s', got %s — check RESPONSE_LIST_PATH", RESPONSE_LIST_PATH, type(listing))
            break

        auctions.extend(listing)

        # Pagination
        total_pages = _dig(data, RESPONSE_TOTAL_PAGES_PATH) if RESPONSE_TOTAL_PAGES_PATH else None
        if total_pages is None or page >= int(total_pages):
            break
        page += 1
        time.sleep(0.3)  # be polite to the API

    log.info("Fetched %d auction entries across %d page(s).", len(auctions), page)
    return auctions

# ── Processing ─────────────────────────────────────────────────────────────────

def process_auctions(auctions: list[dict]):
    poll_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    csv_rows = []

    for entry in auctions:
        raw_name = entry.get(FIELD_ITEM_NAME, "")
        if not raw_name:
            continue

        matched = _matches_tracked(str(raw_name))
        if not matched:
            continue

        try:
            price = float(entry.get(FIELD_PRICE, 0))
            amount = max(1, int(entry.get(FIELD_AMOUNT, 1)))
        except (ValueError, TypeError):
            continue

        unit_price = price / amount if PRICE_IS_TOTAL else price
        seller = entry.get(FIELD_SELLER, "unknown")
        ts_raw = entry.get(FIELD_TIMESTAMP) if FIELD_TIMESTAMP else None
        item_ts = _parse_timestamp(ts_raw)

        # Update daily stats
        stat = daily_stats[matched]
        stat["samples"] += 1

        if stat["min"] is None or unit_price < stat["min"][0]:
            stat["min"] = (unit_price, item_ts, seller)
            log.info("NEW LOW  %-20s  %.2f  (was %s)  seller=%s",
                     raw_name, unit_price,
                     f"{stat['min'][0]:.2f}" if stat["min"] else "—", seller)

        if stat["max"] is None or unit_price > stat["max"][0]:
            stat["max"] = (unit_price, item_ts, seller)
            log.info("NEW HIGH %-20s  %.2f  seller=%s", raw_name, unit_price, seller)

        csv_rows.append({
            "timestamp": item_ts,
            "item": raw_name,
            "unit_price": f"{unit_price:.4f}",
            "amount": amount,
            "seller": seller,
            "poll_time": poll_time,
        })

    if csv_rows:
        append_csv(csv_rows)
        log.info("Logged %d matching entries to %s.", len(csv_rows), LOG_FILE)

# ── Discord ────────────────────────────────────────────────────────────────────

ITEM_EMOJIS = {
    "totem":        "🛡️",
    "ender_pearl":  "🟣",
    "shulker":      "📦",
    "shulker_shell":"🐚",
    "end_crystal":  "💎",
}

def send_discord_summary(title: str = "Daily Price Summary"):
    if not DISCORD_WEBHOOK_URL:
        log.info("Discord webhook not configured — skipping notification.")
        return

    webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, username="Donut Price Bot")
    embed = DiscordEmbed(
        title=f"📊 {title}",
        description=f"Tracking period ending {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}",
        color="f4a460",
    )

    if not daily_stats:
        embed.add_embed_field(name="No data yet", value="No matching auctions found this period.", inline=False)
    else:
        for keyword in TRACKED_ITEMS:
            stat = daily_stats.get(keyword)
            if not stat or stat["samples"] == 0:
                continue

            emoji = ITEM_EMOJIS.get(keyword, "📌")
            label = keyword.replace("_", " ").title()

            min_p, min_t, min_s = stat["min"] if stat["min"] else (None, "—", "—")
            max_p, max_t, max_s = stat["max"] if stat["max"] else (None, "—", "—")

            lines = [f"Samples: **{stat['samples']}**"]
            if min_p is not None:
                lines.append(f"🟢 Lowest:  **{min_p:,.2f}** coins  @ `{min_t}`  (seller: `{min_s}`)")
            if max_p is not None:
                lines.append(f"🔴 Highest: **{max_p:,.2f}** coins  @ `{max_t}`  (seller: `{max_s}`)")

            embed.add_embed_field(
                name=f"{emoji} {label}",
                value="\n".join(lines),
                inline=False,
            )

    embed.set_footer(text="Donut SMP Price Bot • CSV log: price_log.csv")
    embed.set_timestamp()
    webhook.add_embed(embed)

    response = webhook.execute()
    if hasattr(response, "status_code"):
        log.info("Discord response: %s", response.status_code)
    else:
        for r in response:
            log.info("Discord response: %s", r.status_code)


def send_discord_alert(keyword: str, kind: str, price: float, ts: str, seller: str):
    """Instant alert when a new all-time daily low or high is hit."""
    if not DISCORD_WEBHOOK_URL:
        return

    emoji = "🟢" if kind == "LOW" else "🔴"
    label = keyword.replace("_", " ").title()

    webhook = DiscordWebhook(url=DISCORD_WEBHOOK_URL, username="Donut Price Bot")
    embed = DiscordEmbed(
        title=f"{emoji} New Daily {kind}: {label}",
        description=f"**{price:,.2f} coins**\nTime: `{ts}`\nSeller: `{seller}`",
        color="2ecc71" if kind == "LOW" else "e74c3c",
    )
    embed.set_timestamp()
    webhook.add_embed(embed)
    webhook.execute()

# ── Scheduled jobs ─────────────────────────────────────────────────────────────

def poll_job():
    log.info("── Poll started ──────────────────────────────────────────────────────")
    auctions = fetch_all_auctions()
    if auctions:
        process_auctions(auctions)
    log.info("── Poll complete ─────────────────────────────────────────────────────")


def daily_report_job():
    log.info("Sending daily report...")
    send_discord_summary("Daily Price Summary")
    reset_daily_stats()


# ── Entry point ────────────────────────────────────────────────────────────────

def main():
    log.info("Donut SMP Price Bot starting up.")
    log.info("Tracking items: %s", ", ".join(TRACKED_ITEMS))
    log.info("Poll interval: %d minutes", POLL_INTERVAL)
    log.info("Daily report at: %02d:00", DAILY_REPORT_HOUR)

    ensure_csv()

    # Run once immediately on start
    poll_job()

    schedule.every(POLL_INTERVAL).minutes.do(poll_job)
    schedule.every().day.at(f"{DAILY_REPORT_HOUR:02d}:00").do(daily_report_job)

    log.info("Scheduler running. Press Ctrl+C to stop.")
    try:
        while True:
            schedule.run_pending()
            time.sleep(10)
    except KeyboardInterrupt:
        log.info("Interrupted — sending final summary...")
        send_discord_summary("Final Summary (Bot Stopped)")
        log.info("Bot stopped.")


if __name__ == "__main__":
    main()
