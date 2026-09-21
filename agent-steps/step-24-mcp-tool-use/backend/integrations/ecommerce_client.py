"""Step 24：古河商城后端只读客户端。业务事实读取集中在集成层。"""

from __future__ import annotations

import os
from typing import Any

import httpx

from api.schemas import ToolExecutionError
from config.settings import DEFAULT_ECOMMERCE_BASE_URL
from tools.runtime_context import order_user_id


def ecommerce_base_url() -> str:
    """解析古河商城后端地址。"""
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
    """调用古河商城后端只读接口并返回 data。"""
    try:
        response = httpx.get(
        f"{ecommerce_base_url()}{path}",
        headers=delegated_service_headers(delegated_user_id),
        timeout=5,
    )
        response.raise_for_status()
    except httpx.TimeoutException as error:
        raise ToolExecutionError("timeout", "业务接口查询超时。") from error
    except httpx.HTTPStatusError as error:
        status_code = error.response.status_code
        if status_code == 404:
            raise ToolExecutionError("not_found", "业务接口没有查到对应记录。") from error
        if status_code == 403:
            raise ToolExecutionError("forbidden", "当前用户无权查看这条业务记录。") from error
        raise ToolExecutionError("model_unavailable", "业务接口暂时不可用。") from error
    except httpx.RequestError as error:
        raise ToolExecutionError("model_unavailable", "业务接口暂时不可用。") from error
    payload = response.json()
    if not isinstance(payload, dict) or payload.get("success") is not True:
        return None
    return payload.get("data")

def order_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> tuple[dict[str, Any] | None, bool]:
    """优先从运行时上下文或古河业务后端读取订单事实。"""
    order = ecommerce_get(f"/api/orders/{target_order_no}", delegated_user_id=current_user_id)
    if not isinstance(order, dict):
        return None, True
    owner = order_user_id(order)
    return order, not owner or owner == current_user_id

def logistics_fact_from_ecommerce(target_order_no: str, current_user_id: str) -> dict[str, Any] | None:
    """优先从订单事实和业务后端读取物流状态。"""
    logistics = ecommerce_get(f"/api/orders/{target_order_no}/logistics", delegated_user_id=current_user_id)
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

def product_fact_from_ecommerce(sku: str) -> dict[str, Any] | None:
    """按商品标识读取单个商品事实，供库存和活动回答使用。"""
    for product in product_catalog_from_ecommerce():
        if str(product.get("code") or "").upper() == sku.upper():
            return product
    return None

def normalize_product_fact(product: dict[str, Any]) -> dict[str, Any]:
    """把业务后端商品字段整理成步骤快照稳定使用的事实结构。"""
    promotion = product.get("promotion") if isinstance(product.get("promotion"), dict) else {}
    return {
        "sku": product.get("code") or product.get("sku"),
        "name": product.get("name"),
        "inventory": product.get("stock"),
        "current_price": product.get("price"),
        "promotion_price": promotion.get("promotionPrice"),
    }
