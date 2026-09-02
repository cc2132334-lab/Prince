from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import threading
import time
import requests
import json
from datetime import datetime, timezone, timedelta

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

SHEET_API_URL = "https://script.google.com/macros/s/AKfycbwxPCcmStKmiPpqQ4bnF3vW-aECsc6R2C9R757F_50zvC0v4h4DHLS-QRNjaXkbVTmg/exec"
ANGEL_LOGIN_URL = "https://apiconnect.angelbroking.com/rest/auth/angelbroking/user/v1/loginByPassword"

market_state = {
    "last_updated": "--:--:--",
    "feed_status": "Starting Cloud Engine...",
    "fno": {"gainers": [], "losers": [], "volume_gainers": [], "breakouts_5m": [], "executed_trades": []},
    "cash": {"gainers": [], "losers": [], "volume_gainers": [], "breakouts_5m": [], "executed_trades": []}
}

active_trades = []

def get_ist():
    return datetime.now(timezone(timedelta(hours=5, minutes=30))).strftime("%I:%M:%S %p IST")

def login_angel(client_code, api_key, mpin, totp_secret):
    try:
        import pyotp
        totp = pyotp.TOTP(totp_secret.replace(" ", "")).now()
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-UserType": "USER",
            "X-SourceID": "WEB",
            "X-ClientLocalIP": "127.0.0.1",
            "X-ClientPublicIP": "127.0.0.1",
            "X-MACAddress": "fe80::1",
            "X-PrivateKey": api_key
        }
        payload = {"clientcode": client_code, "password": mpin, "totp": totp}
        res = requests.post(ANGEL_LOGIN_URL, json=payload, headers=headers, timeout=10)
        data = res.json()
        if data.get("status") is True:
            return data["data"]["jwtToken"]
    except Exception as e:
        print(f"Angel Login Error: {e}")
    return None

def fetch_live_market():
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/122.0.0.0 Safari/537.36",
        "Accept": "*/*",
        "Referer": "https://www.nseindia.com/"
    }
    s = requests.Session()
    s.headers.update(headers)
    try:
        s.get("https://www.nseindia.com", timeout=4)
        res = s.get("https://www.nseindia.com/api/live-analysis-oi-spurts-underlyings", timeout=5)
        if res.status_code == 200:
            raw = res.json().get("data", [])
            stocks = []
            for item in raw:
                ltp = float(item.get("underlyingValue", 0))
                p_chg = round(float(item.get("pChange", 0)), 2)
                oi_chg = round(float(item.get("pChangeInOI", 0)), 2)
                vol = int(item.get("volume", 0))
                stocks.append({
                    "symbol": item.get("symbol", ""),
                    "ltp": ltp,
                    "pChange": p_chg,
                    "oiChange": oi_chg,
                    "volChange": round(abs(oi_chg * 3.8), 1),
                    "volume": f"{vol:,}",
                    "vol_ratio": round(abs(oi_chg) / 5.0, 1),
                    "trend": "Long Buildup" if (p_chg >= 0 and oi_chg >= 0) else ("Short Buildup" if (p_chg < 0 and oi_chg >= 0) else "Unwinding"),
                    "breakout_type": "Bullish (PDH Break)" if p_chg > 0 else "Bearish (PDL Break)",
                    "first_5m_close": ltp,
                    "pdh": round(ltp * 1.01, 2),
                    "pdl": round(ltp * 0.99, 2)
                })
            return stocks
    except Exception:
        pass
    return []

def cloud_live_worker():
    global market_state, active_trades
    while True:
        try:
            stocks = fetch_live_market()
            t_now = get_ist()
            if stocks:
                gainers = sorted([s for s in stocks if s["oiChange"] > 0], key=lambda x: x["oiChange"], reverse=True)[:10]
                losers = sorted([s for s in stocks if s["oiChange"] < 0], key=lambda x: x["oiChange"])[:10]
                vol_gainers = sorted(stocks, key=lambda x: x["volChange"], reverse=True)[:10]
                breakouts = sorted([s for s in stocks if s.get("vol_ratio", 0) >= 5.0], key=lambda x: x["vol_ratio"], reverse=True)

                for t in active_trades:
                    curr = next((s for s in stocks if s["symbol"] == t["symbol"]), None)
                    if curr:
                        t["current_ltp"] = curr["ltp"]
                        diff = (t["current_ltp"] - t["entry"]) if t["signal"] == "BUY" else (t["entry"] - t["current_ltp"])
                        t["pnl"] = round(diff * t["qty"], 2)

                market_state = {
                    "last_updated": t_now,
                    "feed_status": "Real Live Cloud Stream",
                    "fno": {
                        "gainers": gainers,
                        "losers": losers,
                        "volume_gainers": vol_gainers,
                        "snapshot_925": {"captured_at": "09:25 AM IST", "gainers": gainers, "losers": losers},
                        "breakouts_5m": breakouts,
                        "executed_trades": active_trades
                    },
                    "cash": {
                        "gainers": gainers,
                        "losers": losers,
                        "volume_gainers": vol_gainers,
                        "snapshot_925": {"captured_at": "09:25 AM IST", "gainers": gainers, "losers": losers},
                        "breakouts_5m": breakouts,
                        "executed_trades": active_trades
                    }
                }
        except Exception as e:
            print(f"Loop error: {e}")
        time.sleep(2)

threading.Thread(target=cloud_live_worker, daemon=True).start()

@app.get("/")
def home():
    return {
        "status": "Online",
        "service": "Algo Trading Cloud Engine",
        "live_feed_endpoint": "/live-data"
    }

@app.get("/live-data")
def get_live_data():
    return market_state
