"""Prompt 加载层，集中管理项目 Prompt 文档、片段和上下文统计。"""

from __future__ import annotations

import json

from api.schemas import ChatRequest, Intent, IntentResult, PromptFragment
from config.settings import PROMPT_REGISTRY_PATH


def load_prompt_registry() -> list[PromptFragment]:
    """从 `prompt_registry.json` 读取当前步可用的 Prompt 片段。"""

    # 项目重点：Step 07 继续复用 Step 06 的 Prompt Registry，新增的是成本观察而不是新 Prompt 体系。
    with PROMPT_REGISTRY_PATH.open(encoding="utf-8") as file:
        return [PromptFragment.model_validate(item) for item in json.load(file)]


def select_prompt_fragments(intent: Intent, registry: list[PromptFragment]) -> list[PromptFragment]:
    """按 intent 选择本轮真正进入 Prompt 的片段。"""

    selected = [
        fragment
        for fragment in registry
        # 只统计真正进入 Prompt 的片段，后面的 cost_summary 才能反映本轮真实上下文负担。
        if fragment.enabled and ("all" in fragment.applies_to or intent in fragment.applies_to)
    ]
    return sorted(selected, key=lambda fragment: fragment.priority, reverse=True)


def render_prompt_template(
    request: ChatRequest,
    intent_result: IntentResult,
    fragments: list[PromptFragment],
) -> list[dict[str, str]]:
    """把选中的 Prompt 片段和当前请求渲染成模型 messages。"""

    # 这里仍然渲染 Prompt Registry；成本优化还没开始，本步只是把账单压力看清楚。
    fragment_text = "\n\n".join(
        f"[{fragment.fragment_id} | priority={fragment.priority}]\n{fragment.content}" for fragment in fragments
    )
    system_message = (
        "你是古河商城公司的客服 Agent。当前版本仍使用 Prompt Registry，"
        "但会观察每轮 Prompt 和回答带来的 token 消耗。\n\n"
        f"{fragment_text}"
    )
    user_message = (
        "古河商城系统确认的当前用户事实：\n"
        f"- user_id: {request.runtime_user_id}\n"
        f"- member_level: {request.runtime_member_level or '未提供'}\n"
        f"- risk_level: {request.runtime_risk_level or '未提供'}\n"
        "\n"
        f"粗意图：{intent_result.intent}\n"
        f"粗意图说明：{intent_result.explanation}\n"
        "\n"
        "用户原话：\n"
        f"{request.user_message}"
    )
    return [{"role": "system", "content": system_message}, {"role": "user", "content": user_message}]
