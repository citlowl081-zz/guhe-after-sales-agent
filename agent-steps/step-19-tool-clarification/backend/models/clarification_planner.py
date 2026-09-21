"""Step 19：LLM 澄清规划客户端。模型只产出计划草案，后端仍负责校验和执行。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os
from typing import Any

import httpx

from api.schemas import *
from config.settings import *
from config.settings import _api_key_is_missing
from tools.contracts import *
from tools.planning import build_validated_clarification_plan
from integrations.ecommerce_client import product_catalog_from_ecommerce


def strip_json_fence(content: str) -> str:
    """执行 strip_json_fence 对应的项目逻辑。"""
    text = content.strip()
    if not text.startswith("```"):
        return text
    lines = text.splitlines()
    if lines and lines[0].startswith("```"):
        lines = lines[1:]
    if lines and lines[-1].strip() == "```":
        lines = lines[:-1]
    return "\n".join(lines).strip()

def tool_specs_for_prompt() -> list[dict[str, Any]]:
    """执行 tool_specs_for_prompt 对应的项目逻辑。"""
    return [
        {
            "name": spec.name,
            "description": spec.description,
            "required": spec.required,
            "parameters_schema": spec.parameters_schema,
        }
        for spec in TOOL_SPECS.values()
    ]

def product_catalog_for_prompt() -> list[dict[str, Any]]:
    """执行 product_catalog_for_prompt 对应的项目逻辑。"""
    return product_catalog_from_ecommerce()

def build_clarification_planner_messages(user_message: str) -> list[dict[str, str]]:
    """构造澄清规划 Prompt。

    项目重点：这里开始让 LLM 介入澄清机制，但模型只产出可校验的计划草案，
    不直接调用工具，不读取数据库，也不能替用户选择候选订单。
    """

    schema_hint = {
        "intent": "general_chat | product_consult | order_query | refund_status_query | unknown",
        "tool_name": "get_order_logistics | get_product_inventory | get_refund_status | search_current_user_orders | null",
        "known_arguments": {"order_id": "SO20260420103000001-a1000001", "sku": "SKU-AUD-101", "month": 5},
        "missing_required": ["order_id"],
        "clarification_question": "需要向用户追问的问题，没有则为 null",
        "confidence": 0.0,
        "reason": "一句话说明为什么这样规划",
    }
    system_prompt = (
        "你是古河商城公司客服 Agent 的澄清规划器，只能输出 JSON，不要输出解释。\n"
        "你的任务是识别用户意图、选择候选工具、抽取已知参数、指出缺失必填参数，并给出澄清问题草案。\n"
        "重要边界：不要输出 user_id/current_user_id；不要替用户从多个订单里选择；候选订单必须由后端工具查询；"
        "退款、物流等订单工具必须有 order_id 才能查询具体订单。\n"
        "如果用户没有给订单号但给了月份等低风险查询条件，选择 search_current_user_orders，并把 month 放入 known_arguments。\n"
        "如果用户只是说“我的物流/退款”且没有低风险范围，选择目标只读工具并把 order_id 放入 missing_required。\n"
        "如果用户问商品价格或库存，可根据商品目录把商品名映射到 SKU。\n"
        "输出 JSON 必须符合这个字段形状："
        f"{json.dumps(schema_hint, ensure_ascii=False)}"
    )
    user_prompt = (
        "可用工具：\n"
        f"{json.dumps(tool_specs_for_prompt(), ensure_ascii=False)}\n\n"
        "已知商品目录：\n"
        f"{json.dumps(product_catalog_for_prompt(), ensure_ascii=False)}\n\n"
        f"用户消息：{user_message}"
    )
    return [{"role": "system", "content": system_prompt}, {"role": "user", "content": user_prompt}]

class ClarificationPlannerModelClient:
    """真实调用 LLM，生成 Step 19 的 ClarificationPlan。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        model: str | None = None,
        http_client: httpx.Client | None = None,
    ) -> None:
        """执行 __init__ 对应的项目逻辑。"""
        load_guhe_env()
        self.api_key = api_key if api_key is not None else os.getenv("AGENT_OPENAI_API_KEY")
        self.base_url = (base_url or os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")).rstrip("/")
        self.model = model or os.getenv("AGENT_CLASSIFIER_MODEL") or os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
        self.http_client = http_client

    def plan_clarification(self, request: ChatRequest, tool_specs: dict[str, ToolSpec]) -> ClarificationPlan:
        """执行 plan_clarification 对应的项目逻辑。"""
        if _api_key_is_missing(self.api_key):
            raise RuntimeError("AGENT_OPENAI_API_KEY 未配置，无法调用 LLM 生成澄清规划。")

        request_kwargs = {
            "headers": {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"},
            "json": {
                "model": self.model,
                "messages": build_clarification_planner_messages(request.user_message),
                "temperature": 0,
                "max_tokens": 300,
            },
        }
        if "siliconflow.cn" in self.base_url:
            request_kwargs["json"]["enable_thinking"] = False
        log_model_input(model=self.model, messages=request_kwargs["json"]["messages"], prompt_source=__file__)
        if self.http_client is not None:
            response = self.http_client.post(f"{self.base_url}/chat/completions", **request_kwargs)
        else:
            response = httpx.post(f"{self.base_url}/chat/completions", **request_kwargs, timeout=60)
        response.raise_for_status()
        content = log_model_output(model=self.model, content=response.json()["choices"][0]["message"]["content"])
        payload = json.loads(strip_json_fence(content))
        if not isinstance(payload, dict):
            raise RuntimeError("LLM 澄清规划不是 JSON 对象。")
        return build_validated_clarification_plan(payload, tool_specs, self.model)
