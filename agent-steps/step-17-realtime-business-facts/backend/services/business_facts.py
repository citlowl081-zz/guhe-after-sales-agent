"""Step 17：实时业务事实服务。Step 17 先建立事实接入层，还没有开放 Tool Calling。"""

from __future__ import annotations

from api.schemas import BusinessFactNeed, BusinessFactResult, ChatRequest
from integrations.ecommerce_client import logistics_fact_from_ecommerce, order_fact_from_ecommerce, product_fact_from_ecommerce
from tools.runtime_context import find_context_order, order_no, order_status
from tools.tool_runtime import summarize_order_logistics


class BusinessFactService:
    """真实业务事实服务：只读电商后端校验过的订单、物流和商品事实。"""

    def lookup(self, need: BusinessFactNeed, request: ChatRequest) -> BusinessFactResult:
        """按当前用户身份读取实时业务事实。"""
        if need.kind in {"order", "logistics", "refund_status"}:
            if not need.order_id:
                return BusinessFactResult(
                    source="business_fact_service",
                    found=False,
                    summary="需要订单号才能查询这类实时事实。",
                )
            order = find_context_order(request, need.order_id)
            user_matched = True
            if order is None:
                order, user_matched = order_fact_from_ecommerce(need.order_id, request.runtime_user_id)
            if not user_matched:
                return BusinessFactResult(
                    source="ecommerce_backend",
                    found=False,
                    user_matched=False,
                    summary="订单不属于当前登录用户，不能把订单事实交给模型回答。",
                )
            if order is None:
                return BusinessFactResult(
                    source="ecommerce_backend",
                    found=False,
                    summary="没有查到这个订单号对应的订单。",
                )
            if need.kind == "logistics":
                logistics = logistics_fact_from_ecommerce(need.order_id, request.runtime_user_id)
                summary = summarize_order_logistics(order, logistics)
                return BusinessFactResult(
                    source="ecommerce_backend",
                    found=True,
                    summary=summary,
                    facts={"order": order, "logistics": logistics},
                )
            return BusinessFactResult(
                source="ecommerce_backend",
                found=True,
                summary=f"{order_no(order)} 当前订单状态是{order_status(order) or '待查'}。",
                facts={"order": order},
            )

        if need.kind == "product" and need.sku:
            product = product_fact_from_ecommerce(need.sku)
            if product is None:
                return BusinessFactResult(source="ecommerce_backend", found=False, summary="没有查到这个商品。")
            return BusinessFactResult(
                source="ecommerce_backend",
                found=True,
                summary=f"{product.get('name')} 当前库存 {product.get('stock')} 件，当前价 {product.get('price')} 元。",
                facts={"product": product},
            )

        return BusinessFactResult(source="ecommerce_backend", found=False, summary="没有足够业务参数可查。")
