"""
5 Mins Momo Trades — Scanner Backend
Modelled on working StrikeTrail/ProfitMaster pattern
"""

import os, json
from datetime import datetime, date, timedelta, timezone
from flask import Flask, jsonify, request, redirect
from flask_cors import CORS
from fyers_apiv3 import fyersModel, fyersModel as SessionModel

app = Flask(__name__)
CORS(app)

APP_ID       = os.environ.get("FYERS_APP_ID", "EMRCD1JW93-100")
SECRET_ID    = os.environ.get("FYERS_SECRET_ID", "VZKGCP1AA6")
REDIRECT_URI = os.environ.get("REDIRECT_URI", "https://five-mins-momo-scanner.onrender.com/callback")

# ── Token store (in-memory + file) ────────────────────────────
token_data = {"access_token": None, "token_time": None}

def save_token(token):
    token_data["access_token"] = token
    token_data["token_time"]   = datetime.now().isoformat()
    try:
        with open("/tmp/fyers_token.json", "w") as f:
            json.dump(token_data, f)
    except Exception:
        pass

def load_token():
    try:
        with open("/tmp/fyers_token.json") as f:
            data = json.load(f)
            token_data["access_token"] = data.get("access_token")
            token_data["token_time"]   = data.get("token_time")
    except Exception:
        pass

load_token()

# ── Strategy Parameters ───────────────────────────────────────
PARAMS = {
    "ema_period": 20, "macd_fast": 12, "macd_slow": 26,
    "macd_signal": 9, "entry_offset": 15, "stop_buffer": 20,
    "macd_bars": 5, "timeframe": 5, "rr_ratio": 2.5,
}

INSTRUMENTS = {
    "NIFTY50"  : "NSE:NIFTY50-INDEX",
    "NIFTYBANK": "NSE:NIFTYBANK-INDEX",
    "SENSEX"   : "BSE:SENSEX-INDEX",
}

TRADES_FILE = "/tmp/trades.json"

def load_trades():
    try:
        with open(TRADES_FILE) as f:
            return json.load(f)
    except Exception:
        return []

def save_trades(trades):
    try:
        with open(TRADES_FILE, "w") as f:
            json.dump(trades, f, indent=2, default=str)
    except Exception:
        pass

def get_client():
    return fyersModel.FyersModel(
        client_id=APP_ID, is_async=False,
        token=token_data["access_token"], log_path=""
    )

# ── Auth Routes (same pattern as StrikeTrail) ─────────────────
@app.route("/refresh")
def refresh_token():
    """Redirect to Fyers login page."""
    from fyers_apiv3 import fyersModel as fm
    session = fm.SessionModel(
        client_id=APP_ID,
        secret_key=SECRET_ID,
        redirect_uri=REDIRECT_URI,
        response_type="code",
        grant_type="authorization_code"
    )
    auth_url = session.generate_authcode()
    return redirect(auth_url)

@app.route("/callback")
def callback():
    """Fyers redirects here with auth_code — exchange for access token."""
    auth_code = request.args.get("auth_code", "")
    if not auth_code:
        return "<h2>❌ No auth code received</h2><a href='/'>← Back</a>", 400
    try:
        from fyers_apiv3 import fyersModel as fm
        session = fm.SessionModel(
            client_id=APP_ID,
            secret_key=SECRET_ID,
            redirect_uri=REDIRECT_URI,
            response_type="code",
            grant_type="authorization_code"
        )
        session.set_token(auth_code)
        resp = session.generate_token()
        if resp.get("access_token"):
            save_token(resp["access_token"])
            return """
            <html><body style="font-family:sans-serif;text-align:center;padding:50px;background:#1a2a4a;color:white">
            <h1>✅ Token Refreshed!</h1>
            <p>5 Mins Momo Scanner is ready.</p>
            <a href="/" style="color:#22c55e;font-size:18px">← Go to Scanner</a>
            </body></html>
            """
        return f"<h2>❌ Token exchange failed: {resp.get('message','')}</h2>", 400
    except Exception as e:
        return f"<h2>❌ Error: {e}</h2>", 500

# ── Data & Indicators ─────────────────────────────────────────
def fetch_bars(symbol, resolution=5, bars=200):
    try:
        fyers = get_client()
        today = date.today().strftime("%Y-%m-%d")
        ago   = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        resp  = fyers.history(data={
            "symbol": symbol, "resolution": str(resolution),
            "date_format": "1", "range_from": ago,
            "range_to": today, "cont_flag": "1"
        })
        if resp.get("s") != "ok" or not resp.get("candles"):
            return None, resp.get("message", "Unknown error")
        IST = timezone(timedelta(hours=5, minutes=30))
        candles = []
        for c in resp["candles"]:
            ts = datetime.fromtimestamp(c[0], tz=IST)
            h, m = ts.hour, ts.minute
            if (h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30):
                candles.append({
                    "dt": ts.strftime("%d %b %H:%M"),
                    "open": c[1], "high": c[2], "low": c[3],
                    "close": c[4], "volume": c[5]
                })
        return (candles[-bars:] if len(candles) > bars else candles), None
    except Exception as e:
        return None, str(e)

def ema_calc(values, period):
    k, result, prev = 2.0 / (period + 1), [], None
    for v in values:
        prev = v if prev is None else v * k + prev * (1 - k)
        result.append(prev)
    return result

def add_indicators(candles, p):
    closes = [c["close"] for c in candles]
    ema_v  = ema_calc(closes, p["ema_period"])
    ml     = [a - b for a, b in zip(ema_calc(closes, p["macd_fast"]), ema_calc(closes, p["macd_slow"]))]
    sl     = ema_calc(ml, p["macd_signal"])
    warmup = p["macd_slow"] + p["macd_signal"]
    result = []
    for i, c in enumerate(candles):
        if i < warmup: continue
        row = dict(c)
        row["ema"] = ema_v[i]; row["macd_line"] = ml[i]
        row["sig_line"] = sl[i]; row["macd_hist"] = ml[i] - sl[i]
        result.append(row)
    return result

def detect_signal(candles, p):
    d = add_indicators(candles, p)
    if len(d) < p["macd_bars"] + 2: return None
    last, prev, n = d[-1], d[-2], p["macd_bars"]
    above_now  = last["close"] > last["ema"]
    above_prev = prev["close"] > prev["ema"]
    cross_up   = above_now and not above_prev
    cross_down = not above_now and above_prev
    hist = [r["macd_hist"] for r in d]
    turned_pos = any(hist[-j] > 0 and hist[-j-1] <= 0 for j in range(1, n) if j+1 <= len(hist))
    turned_neg = any(hist[-j] < 0 and hist[-j-1] >= 0 for j in range(1, n) if j+1 <= len(hist))
    signal = None
    if cross_up   and (turned_pos or last["macd_hist"] > 0): signal = "BUY"
    elif cross_down and (turned_neg or last["macd_hist"] < 0): signal = "SELL"
    if not signal: return None
    offset, buf = p["entry_offset"], p["stop_buffer"]
    ema_val, close = last["ema"], last["close"]
    entry = round(ema_val + offset, 2) if signal == "BUY" else round(ema_val - offset, 2)
    sl_p  = round(ema_val - buf,   2) if signal == "BUY" else round(ema_val + buf,   2)
    risk  = abs(entry - sl_p)
    rr    = p["rr_ratio"]
    t1    = round(entry + risk,      2) if signal == "BUY" else round(entry - risk,      2)
    t2    = round(entry + risk * rr, 2) if signal == "BUY" else round(entry - risk * rr, 2)
    hs    = abs(last["macd_hist"]) / (abs(last["macd_line"]) + 1e-9)
    eg    = abs(close - ema_val) / (ema_val + 1e-9) * 100
    score = min(95, int(60 + hs * 20 + eg * 5))
    return {
        "symbol": "", "direction": signal, "entry": entry, "sl": sl_p,
        "t1": t1, "t2": t2, "rr": f"1:{rr}", "score": score,
        "grade": "A" if score >= 80 else "B" if score >= 65 else "C",
        "ema": round(ema_val, 2), "close": round(close, 2),
        "macd_hist": round(last["macd_hist"], 4), "time": last["dt"],
        "outcome": "pending", "exit_price": None, "pnl": None,
        "scanned_at": datetime.now().isoformat()
    }

def get_scanner_status():
    IST = timezone(timedelta(hours=5, minutes=30))
    now = datetime.now(IST)
    if not token_data["access_token"]: return "NO_TOKEN"
    if now.weekday() >= 5: return "MARKET_CLOSED"
    h, m = now.hour, now.minute
    if (h == 9 and m >= 15) or (10 <= h <= 14) or (h == 15 and m <= 30): return "ACTIVE"
    return "MARKET_CLOSED"

# ── API Routes ────────────────────────────────────────────────
@app.route("/")
def index():
    with open(os.path.join(os.path.dirname(__file__), "index.html")) as f:
        return f.read()

@app.route("/history")
def history():
    with open(os.path.join(os.path.dirname(__file__), "history.html")) as f:
        return f.read()

@app.route("/api/status")
def api_status():
    return jsonify({
        "scanner_status": get_scanner_status(),
        "token_set": bool(token_data["access_token"]),
        "token_time": token_data.get("token_time"),
        "time": datetime.now().isoformat()
    })

@app.route("/api/signals")
def api_signals():
    status = get_scanner_status()
    if status == "NO_TOKEN":
        return jsonify({"scanner_status": "NO_TOKEN", "signals": [], "errors": []})

    signals, errors = [], []
    for name, symbol in INSTRUMENTS.items():
        candles, err = fetch_bars(symbol, resolution=PARAMS["timeframe"])
        if err:   errors.append(f"{name}: {err}"); continue
        if not candles: errors.append(f"{name}: no data"); continue
        sig = detect_signal(candles, PARAMS)
        if sig:
            sig["symbol"] = name
            sig["id"]     = f"{name}_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            signals.append(sig)
            trades = load_trades()
            if sig["id"] not in [t.get("id", "") for t in trades]:
                trades.insert(0, sig)
                save_trades(trades)

    return jsonify({
        "scanner_status": status,
        "signals": signals,
        "errors": errors,
        "scanned_at": datetime.now().strftime("%d %b %Y %H:%M:%S")
    })

@app.route("/api/trades")
def get_trades():
    return jsonify(load_trades())

@app.route("/api/rescan", methods=["POST"])
def rescan():
    trades, updated = load_trades(), 0
    for i, trade in enumerate(trades):
        if trade.get("outcome") in ("target", "stop"): continue
        candles, err = fetch_bars(
            INSTRUMENTS.get(trade["symbol"], trade["symbol"]),
            resolution=PARAMS["timeframe"], bars=500
        )
        if err or not candles: continue
        entry, sl, t1, t2 = trade["entry"], trade["sl"], trade["t1"], trade["t2"]
        direction = trade["direction"]
        highs = [c["high"] for c in candles]
        lows  = [c["low"]  for c in candles]
        if not any(l <= entry <= h for h, l in zip(highs, lows)):
            trade.update({"outcome": "expired", "exit_price": None, "pnl": 0})
        else:
            for h, l in zip(highs, lows):
                if direction == "BUY":
                    if h >= t2: trade.update({"outcome":"target","exit_price":t2,"pnl":round(t2-entry,2)}); break
                    if h >= t1: trade.update({"outcome":"target","exit_price":t1,"pnl":round(t1-entry,2)}); break
                    if l <= sl: trade.update({"outcome":"stop","exit_price":sl,"pnl":round(sl-entry,2)}); break
                else:
                    if l <= t2: trade.update({"outcome":"target","exit_price":t2,"pnl":round(entry-t2,2)}); break
                    if l <= t1: trade.update({"outcome":"target","exit_price":t1,"pnl":round(entry-t1,2)}); break
                    if h >= sl: trade.update({"outcome":"stop","exit_price":sl,"pnl":round(entry-sl,2)}); break
            else:
                trade["outcome"] = "pending"
        trades[i] = trade
        updated += 1
    save_trades(trades)
    return jsonify({"updated": updated, "trades": trades})

@app.route("/api/trades/clear", methods=["POST"])
def clear_trades():
    save_trades([]); return jsonify({"ok": True})

@app.route("/api/trades/<trade_id>", methods=["DELETE"])
def delete_trade(trade_id):
    save_trades([t for t in load_trades() if t.get("id") != trade_id])
    return jsonify({"ok": True})

@app.route("/api/stats")
def stats():
    trades = load_trades()
    closed = [t for t in trades if t.get("outcome") in ("target","stop")]
    wins   = [t for t in closed if t.get("outcome") == "target"]
    losses = [t for t in closed if t.get("outcome") == "stop"]
    pnls   = [t.get("pnl", 0) or 0 for t in closed]
    return jsonify({
        "total": len(trades), "closed": len(closed),
        "wins": len(wins), "losses": len(losses),
        "pending": len([t for t in trades if t.get("outcome") == "pending"]),
        "expired": len([t for t in trades if t.get("outcome") == "expired"]),
        "win_rate": round(len(wins)/len(closed)*100,1) if closed else 0,
        "net_pnl": round(sum(pnls),2), "best": max(pnls) if pnls else 0,
        "worst": min(pnls) if pnls else 0,
        "avg_pnl": round(sum(pnls)/len(pnls),2) if pnls else 0,
        "by_symbol": {sym: {
            "wins": len([t for t in closed if t["symbol"]==sym and t["outcome"]=="target"]),
            "losses": len([t for t in closed if t["symbol"]==sym and t["outcome"]=="stop"]),
            "pnl": round(sum(t.get("pnl",0) or 0 for t in closed if t["symbol"]==sym),2),
        } for sym in INSTRUMENTS},
        "by_direction": {
            "BUY": len([t for t in trades if t.get("direction")=="BUY"]),
            "SELL": len([t for t in trades if t.get("direction")=="SELL"]),
        },
        "equity_curve": [{"x":i+1,"y":round(sum(pnls[:i+1]),2)} for i in range(len(pnls))],
        "pnl_distribution": pnls,
    })

if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    app.run(host="0.0.0.0", port=port, debug=False)
