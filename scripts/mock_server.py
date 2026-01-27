# Author: Green Mountain Systems AI Inc.
# Donated to IAB Tech Lab

"""Simple mock OpenDirect REST server for testing."""

import uuid
from datetime import datetime
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI(title="Mock OpenDirect Server", version="2.1.0")

# In-memory storage
products_db: dict[str, dict] = {}
accounts_db: dict[str, dict] = {}
orders_db: dict[str, dict] = {}
lines_db: dict[str, dict] = {}

# Seed some sample products
SAMPLE_PRODUCTS = [
    {
        "id": "prod_homepage_banner",
        "publisherId": "pub_premium_news",
        "name": "Homepage Premium Banner",
        "description": "High-impact banner on homepage",
        "currency": "USD",
        "basePrice": 18.50,
        "rateType": "CPM",
        "deliveryType": "Guaranteed",
        "availableImpressions": 5000000,
        "targeting": {"capabilities": ["geo", "demographic", "contextual"]},
    },
    {
        "id": "prod_video_preroll",
        "publisherId": "pub_streaming_co",
        "name": "Video Pre-Roll 30s",
        "description": "Pre-roll video ad on streaming content",
        "currency": "USD",
        "basePrice": 25.00,
        "rateType": "CPMV",
        "deliveryType": "Guaranteed",
        "availableImpressions": 2000000,
        "targeting": {"capabilities": ["geo", "demographic", "behavioral"]},
    },
    {
        "id": "prod_ctv_spot",
        "publisherId": "pub_ctv_network",
        "name": "CTV 15s Spot",
        "description": "Connected TV ad spot on premium content",
        "currency": "USD",
        "basePrice": 35.00,
        "rateType": "CPM",
        "deliveryType": "PMP",
        "availableImpressions": 1000000,
        "targeting": {"capabilities": ["household", "geo", "interest"]},
    },
    {
        "id": "prod_mobile_interstitial",
        "publisherId": "pub_mobile_apps",
        "name": "Mobile Interstitial",
        "description": "Full-screen mobile app ad",
        "currency": "USD",
        "basePrice": 12.00,
        "rateType": "CPM",
        "deliveryType": "Guaranteed",
        "availableImpressions": 10000000,
        "targeting": {"capabilities": ["device", "geo", "app_category"]},
    },
    {
        "id": "prod_native_content",
        "publisherId": "pub_content_network",
        "name": "Native Content Unit",
        "description": "Native ad in content feed",
        "currency": "USD",
        "basePrice": 8.50,
        "rateType": "CPC",
        "deliveryType": "Guaranteed",
        "availableImpressions": 8000000,
        "targeting": {"capabilities": ["contextual", "interest", "demographic"]},
    },
    {
        "id": "prod_retargeting_display",
        "publisherId": "pub_ad_network",
        "name": "Retargeting Display",
        "description": "Display ads for retargeting campaigns",
        "currency": "USD",
        "basePrice": 6.00,
        "rateType": "CPM",
        "deliveryType": "Guaranteed",
        "availableImpressions": 20000000,
        "targeting": {"capabilities": ["retargeting", "geo", "demographic"]},
    },
]

for p in SAMPLE_PRODUCTS:
    products_db[p["id"]] = p


@app.get("/health")
def health():
    return {"status": "healthy", "version": "2.1.0"}


# Products
@app.get("/products")
def list_products(skip: int = 0, top: int = 50):
    products = list(products_db.values())[skip : skip + top]
    return {"products": products}


@app.get("/products/{product_id}")
def get_product(product_id: str):
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")
    return products_db[product_id]


@app.post("/products/search")
def search_products(filters: dict[str, Any] = None):
    results = list(products_db.values())

    if filters:
        if "channel" in filters:
            # Simple channel matching based on product name/type
            channel = filters["channel"].lower()
            results = [
                p
                for p in results
                if channel in p["name"].lower() or channel in p.get("description", "").lower()
            ]
        if "deliveryType" in filters:
            results = [p for p in results if p["deliveryType"] == filters["deliveryType"]]

    return {"products": results}


@app.post("/products/avails")
def check_avails(request: dict[str, Any]):
    product_id = request.get("productId")
    if product_id not in products_db:
        raise HTTPException(status_code=404, detail="Product not found")

    product = products_db[product_id]
    requested = request.get("requestedImpressions", 1000000)
    available = min(requested, product.get("availableImpressions", 1000000))

    return {
        "productId": product_id,
        "availableImpressions": available,
        "guaranteedImpressions": int(available * 0.95),
        "estimatedCpm": product["basePrice"],
        "totalCost": (available / 1000) * product["basePrice"],
        "deliveryConfidence": 95.0 if available >= requested else 70.0,
        "availableTargeting": product.get("targeting", {}).get("capabilities", []),
    }


# Accounts
@app.get("/accounts")
def list_accounts(skip: int = 0, top: int = 50):
    accounts = list(accounts_db.values())[skip : skip + top]
    return {"accounts": accounts}


@app.post("/accounts")
def create_account(account: dict[str, Any]):
    account_id = f"acct_{uuid.uuid4().hex[:8]}"
    account["id"] = account_id
    accounts_db[account_id] = account
    return account


@app.get("/accounts/{account_id}")
def get_account(account_id: str):
    if account_id not in accounts_db:
        raise HTTPException(status_code=404, detail="Account not found")
    return accounts_db[account_id]


# Orders
@app.get("/accounts/{account_id}/orders")
def list_orders(account_id: str, skip: int = 0, top: int = 50):
    orders = [o for o in orders_db.values() if o.get("accountId") == account_id]
    return {"orders": orders[skip : skip + top]}


@app.post("/accounts/{account_id}/orders")
def create_order(account_id: str, order: dict[str, Any]):
    order_id = f"order_{uuid.uuid4().hex[:8]}"
    order["id"] = order_id
    order["accountId"] = account_id
    order["orderStatus"] = "PENDING"
    orders_db[order_id] = order
    return order


@app.get("/accounts/{account_id}/orders/{order_id}")
def get_order(account_id: str, order_id: str):
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")
    return orders_db[order_id]


@app.patch("/accounts/{account_id}/orders/{order_id}")
def update_order(account_id: str, order_id: str, update: dict[str, Any]):
    if order_id not in orders_db:
        raise HTTPException(status_code=404, detail="Order not found")
    orders_db[order_id].update(update)
    return orders_db[order_id]


# Lines
@app.get("/accounts/{account_id}/orders/{order_id}/lines")
def list_lines(account_id: str, order_id: str, skip: int = 0, top: int = 50):
    lines = [ln for ln in lines_db.values() if ln.get("orderId") == order_id]
    return {"lines": lines[skip : skip + top]}


@app.post("/accounts/{account_id}/orders/{order_id}/lines")
def create_line(account_id: str, order_id: str, line: dict[str, Any]):
    line_id = f"line_{uuid.uuid4().hex[:8]}"
    line["id"] = line_id
    line["orderId"] = order_id
    line["bookingStatus"] = "Draft"
    lines_db[line_id] = line
    return line


@app.get("/accounts/{account_id}/orders/{order_id}/lines/{line_id}")
def get_line(account_id: str, order_id: str, line_id: str):
    if line_id not in lines_db:
        raise HTTPException(status_code=404, detail="Line not found")
    return lines_db[line_id]


@app.patch("/accounts/{account_id}/orders/{order_id}/lines/{line_id}")
def update_line(account_id: str, order_id: str, line_id: str, action: str = None):
    if line_id not in lines_db:
        raise HTTPException(status_code=404, detail="Line not found")

    line = lines_db[line_id]

    if action == "reserve":
        line["bookingStatus"] = "Reserved"
    elif action == "book":
        line["bookingStatus"] = "Booked"
    elif action == "cancel":
        line["bookingStatus"] = "Cancelled"

    return line


@app.get("/accounts/{account_id}/orders/{order_id}/lines/{line_id}/stats")
def get_line_stats(account_id: str, order_id: str, line_id: str):
    if line_id not in lines_db:
        raise HTTPException(status_code=404, detail="Line not found")

    line = lines_db[line_id]
    quantity = line.get("quantity", 1000000)
    rate = line.get("rate", 15.0)

    # Simulate some delivery
    delivered = int(quantity * 0.45)
    spent = (delivered / 1000) * rate

    return {
        "lineId": line_id,
        "impressionsDelivered": delivered,
        "targetImpressions": quantity,
        "deliveryRate": 45.0,
        "pacingStatus": "On Track",
        "amountSpent": spent,
        "budget": (quantity / 1000) * rate,
        "budgetUtilization": 45.0,
        "effectiveCpm": rate,
        "vcr": 78.5,
        "viewability": 72.3,
        "ctr": 0.08,
        "lastUpdated": datetime.utcnow().isoformat(),
    }


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=3000)
