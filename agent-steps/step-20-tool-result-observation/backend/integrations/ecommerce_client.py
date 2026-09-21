"""Step 20：古河商城后端只读客户端。实时订单、物流和商品事实必须从业务系统读取。"""

from __future__ import annotations

import os
from typing import Any

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
    response = httpx.get(
        f"{ecommerce_base_url()}{path}",
        headers=delegated_service_headers(delegated_user_id),
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def order_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> tuple[dict[str, Any] | None, bool]:
    """执行 order_fact_from_ecommerce 对应的项目逻辑。"""
    try:
        order = ecommerce_get(f"/api/orders/{target_order_no}", delegated_user_id=current_user_id)
    except Exception:
        return None, True
    if not isinstance(order, dict):
        return None, True
    return order, str(order.get("userId") or "") == current_user_id

def logistics_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> dict[str, Any] | None:
    """执行 logistics_fact_from_ecommerce 对应的项目逻辑。"""
    try:
        logistics = ecommerce_get(f"/api/orders/{target_order_no}/logistics", delegated_user_id=current_user_id)
    except Exception:
        return None
    return logistics if isinstance(logistics, dict) else None

def product_catalog_from_ecommerce() -> list[dict[str, Any]]:
    """执行 product_catalog_from_ecommerce 对应的项目逻辑。"""
    try:
        products = ecommerce_get("/api/products")
    except Exception:
        return []
    if not isinstance(products, list):
        return []
    return [product for product in products if isinstance(product, dict)]

def product_fact_from_ecommerce(sku: str) -> dict[str, Any] | None:
    """执行 product_fact_from_ecommerce 对应的项目逻辑。"""
    for product in product_catalog_from_ecommerce():
        if str(product.get("code") or "").upper() == sku.upper():
            return product
    return None
