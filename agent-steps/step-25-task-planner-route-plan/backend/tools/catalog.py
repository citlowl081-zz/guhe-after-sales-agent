"""Step 25：工具目录。TaskPlanner 用它收窄本轮候选工具。"""

from __future__ import annotations

from api.schemas import *


class ToolCatalog:
    """本轮可用工具摘要。

    项目重点：TaskPlanner 可以看到工具目录，但每轮只把少量候选能力交给后续链路。
    高风险写动作即使在真实系统存在，也不能进入普通轻路径候选。
    """

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self._tools = {
            "get_order_logistics": ToolCandidate(
                name="get_order_logistics",
                domain="order",
                allowed_in_light_path=True,
                risk_level="low",
                reason="物流查询是只读事实工具，但仍需要订单号和当前用户归属校验。",
            ),
            "search_products": ToolCandidate(
                name="search_products",
                domain="product",
                allowed_in_light_path=True,
                risk_level="low",
                reason="商品搜索用于找到候选商品，不改变业务状态。",
            ),
            "get_product_inventory": ToolCandidate(
                name="get_product_inventory",
                domain="product",
                allowed_in_light_path=True,
                risk_level="low",
                reason="库存和价格是实时业务事实，需要工具读取。",
            ),
            "get_current_user_coupons": ToolCandidate(
                name="get_current_user_coupons",
                domain="promotion",
                allowed_in_light_path=True,
                risk_level="medium",
                reason="当前用户优惠券依赖可信 runtime_user_id。",
            ),
            "after_sale_workflow": ToolCandidate(
                name="after_sale_workflow",
                domain="after_sale",
                allowed_in_light_path=False,
                risk_level="high",
                reason="售后 workflow 是后续受控路径入口，本步只返回路由信号。",
            ),
        }

    def candidate_summaries(self, route_plan: RoutePlan) -> list[ToolCandidate]:
        """按 RoutePlan 取少量候选工具摘要，控制模型可见工具面。"""
        domains = set(route_plan.knowledge_domains)
        if route_plan.intent == "order_logistics":
            domains.add("order")
        if "product" in route_plan.intent:
            domains.add("product")
        if "promotion" in route_plan.intent:
            domains.add("promotion")
        if route_plan.requires_workflow or route_plan.risk_level == "high":
            domains.add("after_sale")

        candidates = [tool for tool in self._tools.values() if tool.domain in domains]
        return candidates or [tool for tool in self._tools.values() if tool.allowed_in_light_path]

    def allowed_required_tools(self, required_tools: list[str], candidates: list[ToolCandidate]) -> list[str]:
        """返回本轮允许进入轻路径的工具名集合。"""
        allowed_names = {tool.name for tool in candidates if tool.allowed_in_light_path or tool.name == "after_sale_workflow"}
        return [tool for tool in required_tools if tool in allowed_names]
