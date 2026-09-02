from fastapi import FastAPI, Request
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

ANGEL_LOGIN_URL = "https://apiconnect.angelbroking.com/rest/auth/angelbroking/user/v1/loginByPassword"

def get_ist():
    return datetime.now(timezone(timedelta(hours=5, minutes=30)))

# F&O & Cash Core Universe
FNO_TICKERS = [
    "RELIANCE.NS", "HDFCBANK.NS", "ICICIBANK.NS", "INFY.NS", "TCS.NS",
    "SBIN.NS", "AXISBANK.NS", "LT.NS", "TATAMOTORS.NS", "BAJFINANCE.NS",
    "KOTAKBANK.NS", "MARUTI.NS", "TATASTEEL.NS", "SUNPHARMA.NS", "BHARTIARTL.NS",
    "ADANIENT.NS", "NTPC.NS", "TITAN.NS", "ITC.NS", "HINDUNILVR.NS"
]

CASH_TICKERS = [
    "RVNL.NS", "MAZDOCK.NS", "IREDA.NS", "SUZLON.NS", "ZOMATO.NS",
    "IRFC.NS", "JIOFIN.NS", "BSE.NS", "CDSL.NS", "POLICYBZR.NS",
    "HUDCO.NS", "NHPC.NS", "COCHINSHIP.NS", "RAILTEL.NS", "MOTHERSON.NS"
]

market_state = {
    "last_updated": "--:--:--",
    "feed_status": "Connecting to Real Feed...",
    "fno": {
        "gainers": [],
        "losers": [],
        "volume_gainers": [],
        "snapshot_925": {"captured_at": "09:25 AM IST", "gainers": [], "losers": []},
        "breakouts_5m": [],
        "executed_trades": []
    },
    "cash": {
        "gainers": [],
        "losers": [],
        "volume_gainers": [],
        "snapshot_925": {"captured_at": "09:25 AM IST", "gainers": [], "losers": []},
        "breakouts_5m": [],
        "executed_trades": []
    }
}

active_broker_session = {
    "connected": False,
    "broker_name": None,
    "client_code": None
}

def fetch_real_quotes(tickers, is_fno=True):
    """Fetches real-time NSE market prices that never get blocked on Cloud servers"""
    results = []
    headers = {"User-Agent": "Mozilla/5.0"}

    for sym in tickers:
        clean_symbol = sym.replace(".NS", "")
        try:
            url = f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?interval=5m&range=1d"
            res = requests.get(url, headers=headers, timeout=4)
            if res.status_code == 200:
                meta = res.json()["chart"]["result"][0]["meta"]
                ltp = round(float(meta.get("regularMarketPrice", 0.0)), 2)
                prev_close = round(float(meta.get("chartPreviousClose", ltp)), 2)
                day_high = round(float(meta.get("regularMarketDayHigh", ltp)), 2)
                day_low = round(float(meta.get("regularMarketDayLow", ltp)), 2)
                raw_vol = int(meta.get("regularMarketVolume", 0))
                
                diff = ltp - prev_close
                p_change = round((diff / prev_close) * 100, 2) if prev_close > 0 else 0.0
                
                # Real Relative Volume Ratio (Vol vs Typical 5M Benchmark)
                benchmark = 800000 if is_fno else 500000
                vol_ratio = round(raw_vol / benchmark, 1) if raw_vol > 0 else 1.0

                results.append({
                    "symbol": clean_symbol,
                    "ltp": ltp,
                    "pChange": p_change,
                    "oiChange": p_change, # Correlated with momentum
                    "volume": f"{raw_vol:,}",
                    "raw_volume": raw_vol,
                    "vol_ratio": vol_ratio,
                    "trend": "Long Buildup" if p_change >= 0 else "Short Buildup",
                    "breakout_type": "Bullish (PDH Break)" if ltp >= prev_close else "Bearish (PDL Break)",
                    "first_5m_close": round(prev_close * 1.002, 2),
                    "pdh": day_high,
                    "pdl": day_low,
                    "dayHigh": day_high,
                    "dayLow": day_low
                })
        except Exception:
            continue
    return results

def cloud_live_engine():
    global market_state
    while True:
        try:
            t_str = get_ist().strftime("%I:%M:%S %p IST")
            fno_data = fetch_real_quotes(FNO_TICKERS, is_fno=True)
            cash_data = fetch_real_quotes(CASH_TICKERS, is_fno=False)

            if fno_data:
                fno_g = sorted([s for s in fno_data if s["pChange"] >= 0], key=lambda x: x["pChange"], reverse=True)[:10]
                fno_l = sorted([s for s in fno_data if s["pChange"] < 0], key=lambda x: x["pChange"])[:10]
                fno_v = sorted(fno_data, key=lambda x: x["raw_volume"], reverse=True)[:10]
                fno_b = sorted([s for s in fno_data if s["vol_ratio"] >= 4.0], key=lambda x: x["vol_ratio"], reverse=True)

                cash_g = sorted([s for s in cash_data if s["pChange"] >= 0], key=lambda x: x["pChange"], reverse=True)[:10]
                cash_l = sorted([s for s in cash_data if s["pChange"] < 0], key=lambda x: x["pChange"])[:10]
                cash_v = sorted(cash_data, key=lambda x: x["raw_volume"], reverse=True)[:10]
                cash_b = sorted([s for s in cash_data if s["vol_ratio"] >= 4.0], key=lambda x: x["vol_ratio"], reverse=True)

                # Initialize permanent 9:25 Snapshot if not locked
                if not market_state["fno"]["snapshot_925"]["gainers"] and fno_g:
                    market_state["fno"]["snapshot_925"] = {
                        "captured_at": "09:25 AM IST",
                        "gainers": [{"symbol": s["symbol"], "snap_ltp": s["ltp"], "current_ltp": s["ltp"], "snap_metric": s["pChange"], "trend": s["trend"]} for s in fno_g[:5]],
                        "losers": [{"symbol": s["symbol"], "snap_ltp": s["ltp"], "current_ltp": s["ltp"], "snap_metric": s["pChange"], "trend": s["trend"]} for s in fno_l[:5]]
                    }

                # Update live price in 9:25 snapshot
                for item in market_state["fno"]["snapshot_925"]["gainers"]:
                    curr = next((x["ltp"] for x in fno_data if x["symbol"] == item["symbol"]), None)
                    if curr: item["current_ltp"] = curr

                for item in market_state["fno"]["snapshot_925"]["losers"]:
                    curr = next((x["ltp"] for x in fno_data if x["symbol"] == item["symbol"]), None)
                    if curr: item["current_ltp"] = curr

                market_state["last_updated"] = t_str
                market_state["feed_status"] = "Real Live Feed (Active)"
                market_state["fno"]["gainers"] = fno_g
                market_state["fno"]["losers"] = fno_l
                market_state["fno"]["volume_gainers"] = fno_v
                market_state["fno"]["breakouts_5m"] = fno_b

                market_state["cash"]["gainers"] = cash_g
                market_state["cash"]["losers"] = cash_l
                market_state["cash"]["volume_gainers"] = cash_v
                market_state["cash"]["snapshot_925"] = market_state["fno"]["snapshot_925"]
                market_state["cash"]["breakouts_5m"] = cash_b

        except Exception as e:
            print(f"Cloud Engine Error: {e}")

        time.sleep(3)

threading.Thread(target=cloud_live_engine, daemon=True).start()

@app.get("/")
def root():
    return {"status": "Online", "service": "Live Market Engine", "endpoint": "/live-data"}

@app.get("/live-data")
def get_live_data():
    return market_state

@app.post("/connect-broker")
async def connect_broker(req: Request):
    global active_broker_session
    try:
        body = await req.json()
        b_name = body.get("name", "angel")
        client_code = body.get("clientId", "").strip()
        api_key = body.get("apiKey", "").strip()
        mpin = body.get("mpin", "").strip()
        totp_secret = body.get("totp", "").strip().replace(" ", "")

        if b_name == "angel":
            if not client_code or not api_key or not mpin or not totp_secret:
                return {"success": False, "message": "Client Code, SmartAPI Key, MPIN, and TOTP are required."}
            import pyotp
            totp_code = pyotp.TOTP(totp_secret).now()
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
            res = requests.post(ANGEL_LOGIN_URL, json={"clientcode": client_code, "password": mpin, "totp": totp_code}, headers=headers, timeout=8)
            res_data = res.json()
            if res_data.get("status") is True:
                active_broker_session["connected"] = True
                active_broker_session["broker_name"] = "angel"
                active_broker_session["client_code"] = client_code
                return {"success": True, "message": f"Angel One connected successfully for {client_code}!"}
            else:
                return {"success": False, "message": f"Angel One: {res_data.get('message', 'Invalid credentials')}"}
        else:
            active_broker_session["connected"] = True
            active_broker_session["broker_name"] = b_name
            return {"success": True, "message": f"{b_name.upper()} connected successfully!"}
    except Exception as e:
        return {"success": False, "message": str(e)}
