from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import hmac, hashlib, time, os
import httpx

app = FastAPI()

# Allow Zapier & browser access
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").encode()
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

BASE_URL = "https://api.binance.com"


def sign_query(params: dict):
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(BINANCE_API_SECRET, query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"


async def binance_request(method, path, params):
    params["timestamp"] = int(time.time() * 1000)
    signed_query = sign_query(params)
    url = f"{BASE_URL}{path}?{signed_query}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

    async with httpx.AsyncClient() as client:
        if method == "POST":
            r = await client.post(url, headers=headers)
        else:
            r = await client.get(url, headers=headers)

    return r.json()


@app.post("/order")
async def order(request: Request):
    if request.headers.get("X-Auth-Token") != AUTH_TOKEN:
        return {"error": "unauthorized"}

    data = await request.json()

    action = data.get("action")
    if action != "OPEN_TRADE":
        return {"error": "invalid_action"}

    symbol = data["symbol"]
    entry = float(data["entry_price"])
    stop = float(data["stop_price"])
    risk_pct = float(data["risk_pct"])

    # Fetch account
    account = await binance_request("GET", "/api/v3/account", {})
    if "balances" not in account:
        return {"error": "binance_account_error", "details": account}

    usdt_balance = next(
        (float(b["free"]) for b in account["balances"] if b["asset"] == "USDT"), 0
    )

    if usdt_balance <= 0:
        return {"error": "no_usdt_balance"}

    risk_amount = usdt_balance * (risk_pct / 100)
    stop_pct = abs(entry - stop) / entry
    if stop_pct == 0:
        return {"error": "invalid_stop"}

    position_size = risk_amount / stop_pct
    qty = round(position_size / entry, 6)

    order_params = {
        "symbol": symbol,
        "side": "BUY",
        "type": "MARKET",
        "quantity": qty,
    }

    result = await binance_request("POST", "/api/v3/order", order_params)

    return {
        "status": "order_sent",
        "qty": qty,
        "response": result
    }
