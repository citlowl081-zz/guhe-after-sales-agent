"""Step 24：MCP 目录层。工具、资源和提示词从这里暴露给可复用工具目录。"""

from __future__ import annotations

from api.schemas import *


class MCPCatalog:
    """本地 MCP 风格目录。

    项目重点：这里展示的是 MCP 作为工具、资源和 Prompt 的标准化来源。
    目录提供能力描述；真正怎么计划、执行、校验和降级，仍然由 Tool Use
    链路和 Hooks 负责。
    """

    def __init__(self) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self._tools = {
            "get_order_logistics": MCPToolDefinition(
                name="get_order_logistics",
                description="查询当前登录用户某个订单的物流状态，只读工具。",
                required=["order_id"],
                parameters_schema={"order_id": "订单号，例如 SO20260420103000001-a1000001"},
                read_only=True,
                risk_level="low",
                resource_uris=["resource://guhe/tools/logistics-boundary"],
                prompt_ids=["prompt://guhe/tool-observation"],
            ),
            "get_product_inventory": MCPToolDefinition(
                name="get_product_inventory",
                description="查询商品当前价格和库存，只读工具。",
                required=["sku"],
                parameters_schema={"sku": "商品 SKU，例如 SKU-AUD-101"},
                read_only=True,
                risk_level="low",
                resource_uris=["resource://guhe/tools/product-boundary"],
                prompt_ids=["prompt://guhe/tool-observation"],
            ),
            "get_refund_status": MCPToolDefinition(
                name="get_refund_status",
                description="查询当前登录用户某个订单的退款进度，只读工具，不执行退款。",
                required=["order_id"],
                parameters_schema={"order_id": "订单号，例如 SO20260420103000001-a1000001"},
                read_only=True,
                risk_level="medium",
                resource_uris=["resource://guhe/tools/refund-status-boundary"],
                prompt_ids=["prompt://guhe/tool-observation"],
            ),
        }
        self._resources = {
            "resource://guhe/tools/logistics-boundary": MCPResource(
                uri="resource://guhe/tools/logistics-boundary",
                title="物流工具边界",
                content="物流工具只返回当前登录用户订单的实时物流事实，不能编造包裹位置，也不能查询他人订单。",
            ),
            "resource://guhe/tools/product-boundary": MCPResource(
                uri="resource://guhe/tools/product-boundary",
                title="商品工具边界",
                content="商品工具只返回实时库存、价格和活动状态，不替代稳定商品知识库，也不承诺最终结算价。",
            ),
            "resource://guhe/tools/refund-status-boundary": MCPResource(
                uri="resource://guhe/tools/refund-status-boundary",
                title="退款进度工具边界",
                content="退款进度工具只查询状态，不创建退款、不取消订单、不批准补偿。",
            ),
            "resource://guhe/high-risk-boundary": MCPResource(
                uri="resource://guhe/high-risk-boundary",
                title="高风险动作边界",
                content="退款、取消订单、补偿和改地址需要后续受控流程和人工确认，不能由普通 Tool Use 自动执行。",
            ),
        }
        self._prompts = {
            "prompt://guhe/tool-observation": MCPPrompt(
                prompt_id="prompt://guhe/tool-observation",
                title="工具 Observation 口径",
                content="把工具返回压缩成事实摘要，保留必要字段和 omitted_fields，不把内部调试字段暴露给用户。",
            ),
            "prompt://guhe/handoff-boundary": MCPPrompt(
                prompt_id="prompt://guhe/handoff-boundary",
                title="高风险转人工口径",
                content="遇到退款、取消订单、补偿等高风险动作时，说明不能自动执行，并建议转人工处理。",
            ),
        }

    def list_tools(self) -> list[MCPToolDefinition]:
        """列出 MCP-style 工具目录，供 Agent 选择前先看能力边界。"""
        return list(self._tools.values())

    def to_tool_specs(self) -> dict[str, ToolSpec]:
        """把目录里的工具批量转换成内部 ToolSpec 列表。"""
        return {name: definition.to_tool_spec() for name, definition in self._tools.items()}

    def read_resource(self, uri: str) -> MCPResource:
        """读取 MCP-style Resource，模拟工具能力附带的稳定业务资料。"""
        return self._resources[uri]

    def get_prompt(self, prompt_id: str) -> MCPPrompt:
        """读取 MCP-style Prompt 片段，展示口径也可以统一维护。"""
        return self._prompts[prompt_id]

    def binding_summary(self, action: ToolAction | None, risk_level: RiskLevel) -> MCPBindingSummary:
        """汇总工具、资源和 Prompt 绑定关系，方便观察 MCP 不是单个函数列表。"""
        if risk_level == "high":
            return MCPBindingSummary(
                tool_source="mcp_catalog",
                selected_tool=None,
                available_tools=sorted(self._tools),
                resources=["resource://guhe/high-risk-boundary"],
                prompts=["prompt://guhe/handoff-boundary"],
                boundary="MCP 提供高风险边界资源和转人工口径，但不替代 HITL 审批。",
            )
        if action is None:
            return MCPBindingSummary(
                tool_source="mcp_catalog",
                selected_tool=None,
                available_tools=sorted(self._tools),
                resources=[],
                prompts=[],
                boundary="本轮没有选中 MCP 工具；Tool Use 仍负责决定是否调用。",
            )
        definition = self._tools[action.tool_name]
        return MCPBindingSummary(
            tool_source="mcp_catalog",
            selected_tool=definition.name,
            available_tools=sorted(self._tools),
            resources=definition.resource_uris,
            prompts=definition.prompt_ids,
            boundary="MCP 负责提供标准化工具说明；Tool Use 仍负责规划、执行、Observation 和 Hooks 治理。",
        )

MCP_CATALOG = MCPCatalog()
