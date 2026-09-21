"""Step 18：把项目工具契约包装成 LangChain StructuredTool。

这里的工具返回值会被整理给调试后台观察 Action / Observation。
这层观察格式服务项目演示，不表示生产系统必须把 LangChain 工具返回封装成同样的响应结构。
"""

from __future__ import annotations

from langchain_core.tools import StructuredTool

from api.schemas import ChatRequest, ToolAction
from tools.contracts import TOOL_SPECS
from tools.tool_runtime import execute_tool_action


def tool_action_reason(tool_name: str) -> str:
    """给前端观察台展示本轮工具调用原因。

    这是调试后台说明文案，帮助学习者理解为什么会选中某个工具；
    真实生产链路可以把同类信息放在 Trace、审计日志或观测平台里。
    """
    reasons = {
        "get_order_logistics": "用户询问订单物流，LangChain 选择实时物流查询工具。",
        "get_product_inventory": "用户询问库存或价格，LangChain 选择商品事实工具。",
        "get_refund_status": "用户询问退款进度，LangChain 选择只读退款状态工具。",
    }
    return reasons.get(tool_name, "LangChain 根据工具描述选择本轮工具。")


def build_langchain_tools(request: ChatRequest) -> list[StructuredTool]:
    """把只读业务工具暴露给 LangChain，并把执行重新收口到后端校验层。

    项目重点：LangChain 看到的是 StructuredTool；每个工具内部仍回到后端校验
    参数和当前用户身份。返回 JSON 是为了让本步调试后台能稳定解析 Observation，
    不是 LangChain Tool Calling 的强制写法。
    """

    def get_order_logistics(order_id: str) -> str:
        """查询当前登录用户某个订单的物流状态。"""
        observation = execute_tool_action(
            ToolAction(
                tool_name="get_order_logistics",
                arguments={"order_id": order_id},
                reason=tool_action_reason("get_order_logistics"),
            ),
            request,
        )
        return observation.model_dump_json()

    def get_product_inventory(sku: str) -> str:
        """查询商品当前价格、库存和活动事实。"""
        observation = execute_tool_action(
            ToolAction(
                tool_name="get_product_inventory",
                arguments={"sku": sku},
                reason=tool_action_reason("get_product_inventory"),
            ),
            request,
        )
        return observation.model_dump_json()

    def get_refund_status(order_id: str) -> str:
        """查询当前登录用户某个订单的退款进度。"""
        observation = execute_tool_action(
            ToolAction(
                tool_name="get_refund_status",
                arguments={"order_id": order_id},
                reason=tool_action_reason("get_refund_status"),
            ),
            request,
        )
        return observation.model_dump_json()

    tool_functions = {
        "get_order_logistics": get_order_logistics,
        "get_product_inventory": get_product_inventory,
        "get_refund_status": get_refund_status,
    }
    return [
        StructuredTool.from_function(
            func=tool_functions[name],
            name=spec.name,
            description=spec.description,
        )
        for name, spec in TOOL_SPECS.items()
    ]
