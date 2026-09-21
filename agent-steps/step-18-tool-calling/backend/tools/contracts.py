"""Step 18：工具契约。模型看到的是工具名、描述和参数 schema，真正执行仍由后端控制。"""

from __future__ import annotations

from api.schemas import ToolSpec


TOOL_SPECS = {
    "get_order_logistics": ToolSpec(
        name="get_order_logistics",
        description="查询当前登录用户某个订单的物流状态，只能用于用户自己的订单。",
        required=["order_id"],
        parameters_schema={"order_id": "古河商城订单号，例如 SO20260420103000001-a1000001"},
    ),
    "get_product_inventory": ToolSpec(
        name="get_product_inventory",
        description="查询商品当前价格、库存和正在参加的活动。",
        required=["sku"],
        parameters_schema={"sku": "商品 SKU，例如 SKU-AUD-101"},
    ),
    "get_refund_status": ToolSpec(
        name="get_refund_status",
        description="查询当前登录用户某个订单的退款进度，只读，不执行退款。",
        required=["order_id"],
        parameters_schema={"order_id": "古河商城订单号，例如 SO20260420103000001-a1000001"},
    ),
}
