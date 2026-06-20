// Donut SMP Trade Logger — Google Apps Script (bound to your Google Sheet)
// Sheet tabs required: Trades | Inventory | Summary
// Script Properties required: BOT_TOKEN, SHEET_ID
//
// Quick setup:
//   1. Run setupHeaders() once from the editor to write header rows.
//   2. Deploy as Web App (Execute as: Me, Access: Anyone).
//   3. Register webhook: curl https://api.telegram.org/bot<TOKEN>/setWebhook?url=<WEB_APP_URL>

const TRADES_TAB    = 'Trades';
const INVENTORY_TAB = 'Inventory';
const SUMMARY_TAB   = 'Summary';

// ── Webhook entry point ──────────────────────────────────────────────────────

function doPost(e) {
  try {
    const update = JSON.parse(e.postData.contents);
    if (!update.message || !update.message.text) return ok();

    const chatId = update.message.chat.id;
    const text   = update.message.text.trim();
    const trade  = parseTrade(text);

    if (!trade) {
      sendMessage(chatId, 'Couldn\'t parse that — try: "bought 5 spawners for 64 each"');
      return ok();
    }
    sendMessage(chatId, logTrade(trade));
  } catch (_) { /* keep webhook alive on errors */ }
  return ok();
}

function ok() {
  return ContentService
    .createTextOutput('{"ok":true}')
    .setMimeType(ContentService.MimeType.JSON);
}

// ── Parser ───────────────────────────────────────────────────────────────────

// Returns { action, item, qty, price } or null.
function parseTrade(text) {
  const lower = text.toLowerCase();

  let action;
  if (/\b(bought|buy|purchased)\b/.test(lower))  action = 'BUY';
  else if (/\b(sold|sell)\b/.test(lower))         action = 'SELL';
  else return null;

  // verb  qty  item-words  for/at  price  [each]
  const m = lower.match(
    /\b(?:bought|buy|purchased|sold|sell)\s+(\d+(?:\.\d+)?)\s+(.+?)\s+(?:for|at)\s+(\d+(?:\.\d+)?)(?:\s+each)?\s*$/
  );
  if (!m) return null;

  return {
    action,
    item:  singularize(m[2].trim()),
    qty:   +m[1],
    price: +m[3],
  };
}

// Title-cases each word; singularizes the last word.
function singularize(phrase) {
  const words = phrase.split(/\s+/);
  const last  = words[words.length - 1];

  let singular;
  if (last.endsWith('ies') && last.length > 4) singular = last.slice(0, -3) + 'y';
  else if (last.endsWith('s') && !last.endsWith('ss')) singular = last.slice(0, -1);
  else singular = last;

  words[words.length - 1] = singular;
  return words.map(w => w[0].toUpperCase() + w.slice(1)).join(' ');
}

// ── Trade logger ─────────────────────────────────────────────────────────────

function logTrade(trade) {
  const ss         = SpreadsheetApp.openById(prop('SHEET_ID'));
  const tradesSheet = ss.getSheetByName(TRADES_TAB);
  const invSheet   = ss.getSheetByName(INVENTORY_TAB);

  // Find this item in Inventory (skip header row at index 0).
  const invData = invSheet.getDataRange().getValues();
  let itemRow = -1, heldQty = 0, avgCost = 0;
  for (let i = 1; i < invData.length; i++) {
    if (invData[i][0].toString().toLowerCase() === trade.item.toLowerCase()) {
      itemRow = i; heldQty = +invData[i][1]; avgCost = +invData[i][2]; break;
    }
  }

  let profit = '';

  if (trade.action === 'BUY') {
    const newQty = heldQty + trade.qty;
    const newAvg = (heldQty * avgCost + trade.qty * trade.price) / newQty;
    setInventory(invSheet, itemRow, trade.item, newQty, newAvg);
  } else {
    // Realized profit = (sell price − avg buy cost) × qty
    profit = (trade.price - avgCost) * trade.qty;
    const newQty = Math.max(0, heldQty - trade.qty);
    setInventory(invSheet, itemRow, trade.item, newQty, avgCost);
  }

  const date = Utilities.formatDate(new Date(), Session.getScriptTimeZone(), 'yyyy-MM-dd HH:mm');
  tradesSheet.appendRow([date, trade.action, trade.item, trade.qty, trade.price, trade.qty * trade.price, profit]);

  updateSummary(ss);
  const netPnl = ss.getSheetByName(SUMMARY_TAB).getRange(2, 3).getValue() || 0;

  if (trade.action === 'BUY') {
    return `✅ Logged: bought ${trade.qty} ${trade.item} @ ${trade.price}. Net P&L: ${fmt(netPnl)}`;
  }
  const sign = profit >= 0 ? '+' : '';
  return `✅ Logged: sold ${trade.qty} ${trade.item} @ ${trade.price} → ${sign}${fmt(profit)} profit. Net P&L: ${fmt(netPnl)}`;
}

// rowIdx is the 0-based index inside getValues() — sheet row = rowIdx + 1.
function setInventory(sheet, rowIdx, item, qty, avgCost) {
  if (rowIdx >= 0) {
    sheet.getRange(rowIdx + 1, 1, 1, 3).setValues([[item, qty, avgCost]]);
  } else {
    sheet.appendRow([item, qty, avgCost]);
  }
}

// Recomputes Total Profit, Total Loss, Net P&L from the Trades tab.
function updateSummary(ss) {
  const rows = ss.getSheetByName(TRADES_TAB).getDataRange().getValues();
  let gain = 0, loss = 0;
  for (let i = 1; i < rows.length; i++) {
    const p = parseFloat(rows[i][6]) || 0;
    if (p > 0) gain += p;
    else if (p < 0) loss += p;
  }
  ss.getSheetByName(SUMMARY_TAB).getRange(2, 1, 1, 3).setValues([[gain, loss, gain + loss]]);
}

// ── Helpers ──────────────────────────────────────────────────────────────────

function fmt(n) {
  return Math.round(n).toString().replace(/\B(?=(\d{3})+(?!\d))/g, ',');
}

function sendMessage(chatId, text) {
  UrlFetchApp.fetch(`https://api.telegram.org/bot${prop('BOT_TOKEN')}/sendMessage`, {
    method:      'post',
    contentType: 'application/json',
    payload:     JSON.stringify({ chat_id: chatId, text }),
  });
}

function prop(key) {
  return PropertiesService.getScriptProperties().getProperty(key);
}

// ── One-time setup ───────────────────────────────────────────────────────────

// Run this once from the Apps Script editor — creates tabs and writes headers.
function setupHeaders() {
  const ss = SpreadsheetApp.openById(prop('SHEET_ID'));
  [
    { name: TRADES_TAB,    headers: ['Date', 'Action', 'Item', 'Qty', 'Unit Price', 'Total', 'Profit'] },
    { name: INVENTORY_TAB, headers: ['Item', 'Qty Held', 'Avg Cost'] },
    { name: SUMMARY_TAB,   headers: ['Total Profit', 'Total Loss', 'Net P&L'] },
  ].forEach(({ name, headers }) => {
    const sheet = ss.getSheetByName(name) || ss.insertSheet(name);
    sheet.getRange(1, 1, 1, headers.length).setValues([headers]);
  });
}
