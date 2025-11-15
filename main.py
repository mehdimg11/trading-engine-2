from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import hmac, hashlib, time, os
import httpx

app = FastAPI()

# -------------------------
# CORS
# -------------------------
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# -------------------------
# ENV VARS
# -------------------------
BINANCE_API_KEY = os.getenv("BINANCE_API_KEY")
BINANCE_API_SECRET = os.getenv("BINANCE_API_SECRET", "").encode()
AUTH_TOKEN = os.getenv("AUTH_TOKEN")

BASE_URL = "https://api.binance.com"


# -------------------------
# SIGNATURE + REQUEST
# -------------------------
def sign_query(params: dict) -> str:
    query = "&".join(f"{k}={v}" for k, v in params.items())
    signature = hmac.new(BINANCE_API_SECRET, query.encode(), hashlib.sha256).hexdigest()
    return f"{query}&signature={signature}"

async def binance_request(method: str, path: str, params: dict):
    params["timestamp"] = int(time.time() * 1000)
    signed_query = sign_query(params)
    url = f"{BASE_URL}{path}?{signed_query}"
    headers = {"X-MBX-APIKEY": BINANCE_API_KEY}

    async with httpx.AsyncClient() as client:
        if method.upper() == "POST":
            r = await client.post(url, headers=headers)
        else:
            r = await client.get(url, headers=headers)
        try:
            return r.json()
        except Exception:
            return {"error": "invalid_json", "text": r.text}


# -------------------------
# HELPERS
# -------------------------
async def get_account():
    return await binance_request("GET", "/api/v3/account", {})

async def get_free_balance(asset: str) -> float:
    account = await get_account()
    if "balances" not in account:
        raise ValueError(f"binance_account_error: {account}")
    for b in account["balances"]:
        if b["asset"] == asset:
            return float(b["free"])
    return 0.0


# -------------------------
# MAIN ORDER ENDPOINT
# -------------------------
@app.post("/order")
async def handle_order(request: Request):

    # 1) Auth check
    client_token = request.headers.get("X-Auth-Token")
    if client_token != AUTH_TOKEN:
        return {"error": "Unauthorized", "received_token": client_token}

    data = await request.json()
    action = data.get("action")
    symbol = data.get("symbol", "BTCUSDT")

    # ---------------------
    # OPEN_TRADE  (Spot BUY)
    # ---------------------
    if action == "OPEN_TRADE":

        entry = float(data["entry_price"])
        stop = float(data["stop_price"])
        risk_pct = float(data["risk_pct"])

        # رصيد USDT المتاح
        usdt_free = await get_free_balance("USDT")
        if usdt_free <= 0:
            return {"error": "no_usdt_balance", "details": {"usdt_free": usdt_free}}

        # حساب حجم الصفقة بناءً على المخاطرة
        risk_cash = usdt_free * (risk_pct / 100.0)
        stop_pct = abs(entry - stop) / entry
        if stop_pct == 0:
            return {"error": "invalid_stop_loss"}

        position_notional = risk_cash / stop_pct
        quantity = position_notional / entry

        # تقريب الكمية لعدد من الخانات (حسب العملة، هنا نستعمل 6 كسهل)
        quantity = round(quantity, 6)

        order_params = {
            "symbol": symbol,
            "side": "BUY",
            "type": "MARKET",
            "quoteOrderQty": round(position_notional, 2)  # أو استعمل "quantity" بدلها حسب تفضيلك
            # "quantity": quantity,
        }

        result = await binance_request("POST", "/api/v3/order", order_params)

        return {
            "status": "OPEN_TRADE_SENT",
            "symbol": symbol,
            "position_notional": position_notional,
            "calculated_qty": quantity,
            "binance_response": result,
        }

    # ---------------------
    # CLOSE_TRADE  (Spot SELL)
    # ---------------------
    if action == "CLOSE_TRADE":
        # مثال: لو الرمز BTCUSDT → الأصل الأساسي BTC
        base_asset = symbol.replace("USDT", "")
        base_free = await get_free_balance(base_asset)

        if base_free <= 0:
            return {"error": "no_position_to_close", "asset": base_asset, "free": base_free}

        quantity = round(base_free, 6)

        order_params = {
            "symbol": symbol,
            "side": "SELL",
            "type": "MARKET",
            "quantity": quantity,
        }

        result = await binance_request("POST", "/api/v3/order", order_params)

        return {
            "status": "CLOSE_TRADE_SENT",
            "symbol": symbol,
            "qty_sold": quantity,
            "binance_response": result,
        }

    # ---------------------
    # Unknown action
    # ---------------------
    return {"error": "Unknown action", "action": action}
