"""Step 25：TaskPlanner 的 LLM 客户端。模型只生成结构化计划草案。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import json
import os

import httpx

try:
    from openai import AsyncOpenAI
except ImportError:  # pragma: no cover - 项目允许在未安装模型依赖时走诚实降级
    AsyncOpenAI = None

from api.schemas import *
from config.settings import *
from config.settings import _api_key_is_missing


def strip_json_fence(content: str) -> str:
    """兼容模型把 JSON 包在 Markdown 代码块里的情况。"""

    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    return text

def build_planner_prompt(user_message: str, tool_candidates: list[dict[str, Any]]) -> str:
    """构造 TaskPlanner 的结构化规划提示词。"""
    tool_candidate_text = ""
    if tool_candidates:
        tool_candidate_text = (
            "\n候选工具只能从下面列表选择，required_tools 不要输出列表外的工具名：\n"
            + json.dumps(tool_candidates, ensure_ascii=False)
            + "\n"
        )
    return (
        "你是古河商城公司客服 Agent 的任务规划器，只能输出 JSON，不要输出解释。\n"
        "字段：intent 字符串；needs_rag 布尔；needs_business_tools 布尔；rag_query 字符串；confidence 0到1；"
        "intents 字符串数组；entity_refs 字符串数组；required_context 字符串数组；required_tools 字符串数组；"
        "knowledge_domains 字符串数组；has_realtime_fact 布尔；risk_level 为 low/medium/high；requires_workflow 布尔；fallback_policy 字符串。\n"
        "判断原则：政策、FAQ、发票、售后规则、配送说明、产品知识需要 RAG；订单、物流、库存、价格、会员优惠是否可用等实时事实需要业务工具。"
        "复合问题可以同时需要 RAG 和业务工具，不能因为包含优惠、会员、政策等词就忽略实时事实工具。\n\n"
        f"{tool_candidate_text}"
        f"用户问题：{user_message}"
    )

class TaskPlannerModelClient:
    """真实调用轻量分类模型，为低置信 RoutePlan 补充结构化候选。"""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        classifier_model: str | None = None,
    ) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        load_guhe_env()
        self.openai_api_key = api_key if api_key is not None else os.getenv("AGENT_OPENAI_API_KEY")
        self.openai_base_url = (base_url or os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")).rstrip("/")
        self.classifier_model = (
            classifier_model
            or os.getenv("AGENT_CLASSIFIER_MODEL")
            or os.getenv("AGENT_OPENAI_MODEL")
            or "Qwen/Qwen3-8B"
        )

    def can_call_model(self) -> bool:
        """判断轻量分类模型配置是否完整，避免无配置时伪造模型结果。"""
        return bool(
            os.getenv("AGENT_GUHE_DISABLE_LLM") != "1"
            and not _api_key_is_missing(self.openai_api_key)
            and self.classifier_model
            and AsyncOpenAI is not None
        )

    async def plan_task(self, user_message: str, tool_candidates: list[dict[str, Any]]) -> dict[str, Any] | None:
        """低置信场景调用 classifier_model。

        项目重点：这里不生成客服回答，不执行工具，也不批准退款；模型只输出 RoutePlan 候选字段。
        后续仍要经过 Pydantic RoutePlan、规则兜底和 ToolCatalog 白名单约束。
        """

        if not self.can_call_model():
            return None

        client = AsyncOpenAI(
            api_key=self.openai_api_key,
            base_url=self.openai_base_url,
            timeout=15,
            max_retries=0,
        )
        try:
            extra_body = {"enable_thinking": False} if "siliconflow.cn" in self.openai_base_url else None
            messages = [{"role": "user", "content": build_planner_prompt(user_message, tool_candidates)}]
            log_model_input(model=self.classifier_model, messages=messages, prompt_source=__file__)
            response = await client.chat.completions.create(
                model=self.classifier_model,
                messages=messages,
                max_tokens=160,
                temperature=0,
                extra_body=extra_body,
            )
            content = response.choices[0].message.content or ""
            log_model_output(model=self.classifier_model, content=content)
            payload = json.loads(strip_json_fence(content))
            return payload if isinstance(payload, dict) else None
        except Exception:
            return None
