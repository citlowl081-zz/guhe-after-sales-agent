"""Step 22：古河商城后端只读客户端。实时订单、物流和商品事实必须从业务系统读取。"""

from __future__ import annotations

import os
from typing import Any

import httpx

from config.settings import DEFAULT_ECOMMERCE_BASE_URL


def ecommerce_base_url() -> str:
    """解析古河商城业务后端地址。"""
    return os.getenv("ECOMMERCE_BASE_URL", os.getenv("AGENT_ECOMMERCE_BASE_URL", DEFAULT_ECOMMERCE_BASE_URL)).rstrip("/")

def ecommerce_get(path: str) -> dict[str, Any] | list[Any] | None:
    """调用古河商城后端只读接口并返回 data 字段。"""
    response = httpx.get(f"{ecommerce_base_url()}{path}", timeout=5)
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
    return [product for product in products if isinstance(product, dict)]

def product_fact_from_ecommerce(sku: str) -> dict[str, Any] | None:
    """执行 product_fact_from_ecommerce 对应的项目逻辑。"""
    for product in product_catalog_from_ecommerce():
        if str(product.get("code") or "").upper() == sku.upper():
            return product
    return None

def normalize_product_fact(product: dict[str, Any]) -> dict[str, Any]:
    """执行 normalize_product_fact 对应的项目逻辑。"""
    promotion = product.get("promotion") if isinstance(product.get("promotion"), dict) else {}
    return {
        "sku": product.get("code") or product.get("sku"),
        "name": product.get("name"),
        "current_price": product.get("price"),
        "promotion_price": promotion.get("promotionPrice"),
        "inventory": product.get("stock"),
        "activity": promotion.get("discountSummary") or product.get("highlights") or "暂无活动信息",
    }
