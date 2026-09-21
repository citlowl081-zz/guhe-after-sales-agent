"""Step 19：古河商城后端只读客户端。实时订单、物流和商品事实必须从业务系统读取。"""

from __future__ import annotations

import os
from typing import Any
from urllib.parse import quote, urlencode

import httpx

from config.settings import DEFAULT_ECOMMERCE_BASE_URL


def ecommerce_base_url() -> str:
    """解析古河商城业务后端地址。"""
    return os.getenv("ECOMMERCE_BASE_URL", os.getenv("AGENT_ECOMMERCE_BASE_URL", DEFAULT_ECOMMERCE_BASE_URL)).rstrip("/")

def delegated_service_headers(current_user_id: str | None) -> dict[str, str]:
    """项目 Agent 只用可信 Runtime Context 用户构造服务身份头。"""
    user_id = str(current_user_id or "").strip()
    token = os.getenv(
        "AGENT_ECOMMERCE_SERVICE_TOKEN",
        os.getenv("AGENT_SERVICE_AUTH_TOKEN", "guhe-debug-agent-service"),
    ).strip()
    if not user_id or not token:
        return {}
    return {"X-Agent-Service-Token": token, "X-Agent-User-Id": user_id}

def ecommerce_get(
    path: str,
    *,
    delegated_user_id: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    """调用古河商城后端只读接口并返回 data 字段。"""
    url = f"{ecommerce_base_url()}{path}"
    response = httpx.get(
        url,
        headers=delegated_service_headers(delegated_user_id),
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def product_catalog_from_ecommerce() -> list[dict[str, Any]]:
    """执行 product_catalog_from_ecommerce 对应的项目逻辑。"""
    try:
        products = ecommerce_get("/api/products")
    except Exception:
        return []
    if not isinstance(products, list):
        return []
    return [
        {
            "id": product.get("id"),
            "sku": str(product.get("code") or "").strip(),
            "name": str(product.get("name") or "").strip(),
            "price": product.get("price"),
            "stock": product.get("stock"),
        }
        for product in products
        if isinstance(product, dict) and product.get("code") and product.get("name")
    ]

def order_fact_from_ecommerce(order_id: str, current_user_id: str) -> dict[str, Any] | None:
    """执行 order_fact_from_ecommerce 对应的项目逻辑。"""
    try:
        order = ecommerce_get(f"/api/orders/{order_id}", delegated_user_id=current_user_id)
    except Exception:
        return None
    if not isinstance(order, dict) or str(order.get("userId") or "") != current_user_id:
        return None
    return order

def current_user_orders_from_ecommerce(
    current_user_id: str,
    *,
    month: int | None = None,
    limit: int = 10,
) -> list[dict[str, Any]]:
    """通过项目调试接口读取业务数据库中的当前用户订单安全摘要。"""
    query = {"limit": max(1, min(limit, 50))}
    if month is not None:
        query["month"] = month
    path = (
        f"/api/guhe-debug/users/{quote(current_user_id, safe='')}/order-context"
        f"?{urlencode(query)}"
    )
    try:
        orders = ecommerce_get(path)
    except Exception:
        return []
    if isinstance(orders, dict):
        orders = orders.get("orders")
    return [order for order in orders if isinstance(order, dict)] if isinstance(orders, list) else []

def logistics_fact_from_ecommerce(order_id: str, current_user_id: str) -> dict[str, Any] | None:
    """执行 logistics_fact_from_ecommerce 对应的项目逻辑。"""
    try:
        logistics = ecommerce_get(f"/api/orders/{order_id}/logistics", delegated_user_id=current_user_id)
    except Exception:
        return None
    return logistics if isinstance(logistics, dict) else None

def product_fact_from_ecommerce(sku: str) -> dict[str, Any] | None:
    """执行 product_fact_from_ecommerce 对应的项目逻辑。"""
    products = product_catalog_from_ecommerce()
    for product in products:
        if product.get("sku") == sku:
            try:
                details = ecommerce_get(f"/api/products/{product.get('id')}")
            except Exception:
                details = None
            return details if isinstance(details, dict) else product
    return None
