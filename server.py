"""
Donut SMP Jamble Mod — local server
Run: python server.py
Open: http://localhost:5000
Data stored in trades.db (SQLite) in the same directory.
"""

import csv
import io
import sqlite3
from datetime import datetime
from flask import Flask, g, jsonify, request, send_file, send_from_directory

app = Flask(__name__, static_folder=".", static_url_path="")
DB = "trades.db"


# ── Database ──────────────────────────────────────────────────────────────────

def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    with sqlite3.connect(DB) as db:
        db.executescript("""
            CREATE TABLE IF NOT EXISTS trades (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                date      TEXT    NOT NULL,
                action    TEXT    NOT NULL,
                item      TEXT    NOT NULL,
                qty       REAL    NOT NULL,
                price     REAL    NOT NULL,
                total     REAL    NOT NULL,
                profit    REAL
            );
            CREATE TABLE IF NOT EXISTS inventory (
                item      TEXT PRIMARY KEY,
                qty       REAL NOT NULL DEFAULT 0,
                avg_cost  REAL NOT NULL DEFAULT 0
            );
        """)


# ── Helpers ───────────────────────────────────────────────────────────────────

def singularize(phrase):
    words = phrase.strip().split()
    last = words[-1].lower()
    if last.endswith("ies") and len(last) > 4:
        last = last[:-3] + "y"
    elif last.endswith("s") and not last.endswith("ss"):
        last = last[:-1]
    words[-1] = last
    return " ".join(w.capitalize() for w in words)


def recalc_summary(db):
    rows = db.execute("SELECT profit FROM trades WHERE profit IS NOT NULL").fetchall()
    gain = sum(r["profit"] for r in rows if r["profit"] > 0)
    loss = sum(r["profit"] for r in rows if r["profit"] < 0)
    return {"totalProfit": gain, "totalLoss": loss, "netPnl": gain + loss}


# ── API ───────────────────────────────────────────────────────────────────────

@app.get("/api/trades")
def list_trades():
    db = get_db()
    rows = db.execute("SELECT * FROM trades ORDER BY id DESC LIMIT 500").fetchall()
    return jsonify([dict(r) for r in rows])


@app.post("/api/trades")
def add_trade():
    data = request.json
    action = data.get("action", "").upper()
    item   = singularize(data.get("item", ""))
    qty    = float(data.get("qty", 0))
    price  = float(data.get("price", 0))

    if action not in ("BUY", "SELL") or not item or qty <= 0 or price < 0:
        return jsonify({"error": "Invalid trade"}), 400

    total  = qty * price
    profit = None
    db     = get_db()
    date   = datetime.now().strftime("%Y-%m-%d %H:%M")

    row = db.execute("SELECT qty, avg_cost FROM inventory WHERE item = ?", (item,)).fetchone()
    held_qty  = row["qty"]       if row else 0.0
    avg_cost  = row["avg_cost"]  if row else 0.0

    if action == "BUY":
        new_qty = held_qty + qty
        new_avg = (held_qty * avg_cost + qty * price) / new_qty
        db.execute(
            "INSERT INTO inventory(item, qty, avg_cost) VALUES(?,?,?) "
            "ON CONFLICT(item) DO UPDATE SET qty=excluded.qty, avg_cost=excluded.avg_cost",
            (item, new_qty, new_avg),
        )
    else:
        profit  = (price - avg_cost) * qty
        new_qty = max(0.0, held_qty - qty)
        db.execute(
            "INSERT INTO inventory(item, qty, avg_cost) VALUES(?,?,?) "
            "ON CONFLICT(item) DO UPDATE SET qty=excluded.qty",
            (item, new_qty, avg_cost),
        )

    db.execute(
        "INSERT INTO trades(date, action, item, qty, price, total, profit) VALUES(?,?,?,?,?,?,?)",
        (date, action, item, qty, price, total, profit),
    )
    db.commit()

    return jsonify({"ok": True, "summary": recalc_summary(db)})


@app.delete("/api/trades/<int:trade_id>")
def delete_trade(trade_id):
    db = get_db()
    db.execute("DELETE FROM trades WHERE id = ?", (trade_id,))
    db.commit()
    # Rebuild inventory from scratch after deletion
    rebuild_inventory(db)
    return jsonify({"ok": True, "summary": recalc_summary(db)})


def rebuild_inventory(db):
    db.execute("DELETE FROM inventory")
    trades = db.execute("SELECT action, item, qty, price FROM trades ORDER BY id").fetchall()
    inv = {}
    for t in trades:
        item, qty, price, action = t["item"], t["qty"], t["price"], t["action"]
        held, avg = inv.get(item, (0.0, 0.0))
        if action == "BUY":
            new_qty = held + qty
            inv[item] = (new_qty, (held * avg + qty * price) / new_qty)
        else:
            inv[item] = (max(0.0, held - qty), avg)
    for item, (qty, avg) in inv.items():
        db.execute(
            "INSERT INTO inventory(item, qty, avg_cost) VALUES(?,?,?)",
            (item, qty, avg),
        )
    db.commit()


@app.get("/api/inventory")
def list_inventory():
    db = get_db()
    rows = db.execute("SELECT * FROM inventory WHERE qty > 0 ORDER BY item").fetchall()
    return jsonify([dict(r) for r in rows])


@app.get("/api/summary")
def summary():
    return jsonify(recalc_summary(get_db()))


@app.get("/api/export")
def export_csv():
    db = get_db()
    rows = db.execute("SELECT date,action,item,qty,price,total,profit FROM trades ORDER BY id").fetchall()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "Action", "Item", "Qty", "Unit Price", "Total", "Profit"])
    for r in rows:
        w.writerow([r["date"], r["action"], r["item"], r["qty"], r["price"], r["total"],
                    "" if r["profit"] is None else round(r["profit"], 2)])
    buf.seek(0)
    return send_file(
        io.BytesIO(buf.getvalue().encode()),
        mimetype="text/csv",
        as_attachment=True,
        download_name="donut_trades.csv",
    )


@app.get("/")
def index():
    return send_from_directory(".", "index.html")


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    init_db()
    print("Donut SMP Jamble Mod running → http://localhost:5000")
    app.run(debug=False, port=5000)
