"""Prompt 加载层，集中管理项目 Prompt 文档、片段和上下文统计。"""

from __future__ import annotations

import json

from api.schemas import ChatRequest, Intent, IntentResult, PromptFragment
from config.settings import PROMPT_REGISTRY_PATH


def load_prompt_registry() -> list[PromptFragment]:
    """从 `prompt_registry.json` 读取当前步可用的 Prompt 片段。"""

    # 项目重点：Prompt 片段成为数据文件，规则改动不再散落在主流程代码里。
    with PROMPT_REGISTRY_PATH.open(encoding="utf-8") as file:
        return [PromptFragment.model_validate(item) for item in json.load(file)]


def select_prompt_fragments(intent: Intent, registry: list[PromptFragment]) -> list[PromptFragment]:
    """按 intent 选择 Prompt 片段，并用 priority 控制顺序。"""

    selected = [
        fragment
        for fragment in registry
        # enabled=False 的旧片段留档但不进入本轮 Prompt，避免历史复盘继续污染当前回答。
        if fragment.enabled and ("all" in fragment.applies_to or intent in fragment.applies_to)
    ]
    return sorted(selected, key=lambda fragment: fragment.priority, reverse=True)


def render_prompt_template(
    request: ChatRequest,
    intent_result: IntentResult,
    fragments: list[PromptFragment],
) -> list[dict[str, str]]:
    """把注册表片段渲染成模型 messages。"""

    # 项目重点：Prompt Registry 管规则片段的选择和顺序，不负责查业务事实。
    fragment_text = "\n\n".join(
        f"[{fragment.fragment_id} | priority={fragment.priority}]\n{fragment.content}" for fragment in fragments
    )
    system_message = (
        "你是古河商城公司的客服 Agent。当前版本使用 Prompt Template 和 Prompt Registry 管理规则片段。\n"
        "请严格按照下列片段顺序回答；高优先级片段覆盖低优先级片段。\n\n"
        f"{fragment_text}"
    )
    # 系统事实、粗意图和用户原话仍放在 user message，避免和规则片段混成一团。
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
