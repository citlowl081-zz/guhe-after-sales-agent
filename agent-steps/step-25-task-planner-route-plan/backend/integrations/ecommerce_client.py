"""Step 25：古河商城后端只读客户端。业务事实读取集中在集成层。"""

from __future__ import annotations

import os
from typing import Any

import httpx

from config.settings import DEFAULT_ECOMMERCE_BASE_URL, load_guhe_env
from tools.runtime_context import order_user_id


def ecommerce_base_url() -> str:
    """解析古河商城后端地址。"""
    return os.getenv("ECOMMERCE_BASE_URL", os.getenv("AGENT_ECOMMERCE_BASE_URL", DEFAULT_ECOMMERCE_BASE_URL)).rstrip("/")

def delegated_service_headers(current_user_id: str | None) -> dict[str, str]:
    """项目 Agent 只用可信 Runtime Context 用户构造服务身份头。"""
    load_guhe_env()
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
    params: dict[str, str] | None = None,
    *,
    delegated_user_id: str | None = None,
) -> dict[str, Any] | list[Any] | None:
    """调用古河商城后端只读接口并返回 data。"""
    response = httpx.get(
        f"{ecommerce_base_url()}{path}",
        params=params,
        headers=delegated_service_headers(delegated_user_id),
        timeout=5,
    )
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def order_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> tuple[dict[str, Any] | None, bool]:
    """优先从运行时上下文或古河业务后端读取订单事实。"""
    try:
        order = ecommerce_get(f"/api/orders/{target_order_no}", delegated_user_id=current_user_id)
    except Exception:
        return None, True
    if not isinstance(order, dict):
        return None, True
    owner = order_user_id(order)
    return order, not owner or owner == current_user_id

def logistics_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> dict[str, Any] | None:
    """优先从订单事实和业务后端读取物流状态。"""
    try:
        logistics = ecommerce_get(f"/api/orders/{target_order_no}/logistics", delegated_user_id=current_user_id)
    except Exception:
        return None
    return logistics if isinstance(logistics, dict) else None

def product_catalog_from_ecommerce() -> list[dict[str, Any]]:
    """从古河业务后端读取商品候选，作为工具路由和商品咨询事实来源。"""
    try:
        products = ecommerce_get("/api/products")
    except Exception:
        return []
    if not isinstance(products, list):
        return []
    return [product for product in products if isinstance(product, dict)]


def user_coupons_from_ecommerce(
    current_user_id: str,
    product_category: str | None = None,
) -> tuple[list[dict[str, Any]], bool]:
    """按可信 Runtime Context 用户查询真实优惠券，并区分空结果和依赖失败。"""

    try:
        coupons = ecommerce_get(
            f"/api/users/{current_user_id}/coupons",
            params={"productCategory": product_category} if product_category else None,
            delegated_user_id=current_user_id,
        )
    except Exception:
        return [], False
    if not isinstance(coupons, list):
        return [], False
    return [coupon for coupon in coupons if isinstance(coupon, dict)], True
