"""成本观察层，估算 token、费用和 usage 明细，展示 Prompt 变长后的成本压力。"""

from __future__ import annotations

import os
from typing import Any, Literal

from api.schemas import CostSummary, TokenUsage
from config.settings import DEFAULT_INPUT_CNY_PER_1K, DEFAULT_OUTPUT_CNY_PER_1K


def estimate_tokens(text: str) -> int:
    """模型平台未返回 usage 时，用本地估算兜底观察趋势。"""

    # 项目重点：这是趋势估算，不是 tokenizer。真实计费仍以模型平台 usage 为准。
    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def estimate_messages_tokens(messages: list[dict[str, str]]) -> tuple[int, int]:
    """估算整组 messages 的 token 数，并返回原始字符长度。"""

    text = "\n".join(f"{message['role']}:{message['content']}" for message in messages)
    return estimate_tokens(text), len(text)


def _read_usage_int(usage: dict[str, Any], *names: str) -> int | None:
    """从不同平台可能使用的 usage 字段名中读取整数值。"""

    for name in names:
        raw_value = usage.get(name)
        if raw_value is None:
            continue
        try:
            return int(raw_value)
        except (TypeError, ValueError):
            continue
    return None


def parse_model_usage(payload: dict[str, Any]) -> TokenUsage | None:
    """把模型响应里的 usage 标准化成项目统一的 TokenUsage。"""

    usage = payload.get("usage")
    if not isinstance(usage, dict):
        # OpenAI-compatible 服务不一定返回 usage；没有 usage 时后续会走 local_estimate。
        return None

    # 兼容不同平台的字段名，但统一成可观察的 prompt/answer/total 三类 token。
    prompt_tokens = _read_usage_int(usage, "prompt_tokens", "input_tokens")
    answer_tokens = _read_usage_int(usage, "completion_tokens", "output_tokens", "answer_tokens")
    total_tokens = _read_usage_int(usage, "total_tokens")
    if answer_tokens is None and prompt_tokens is not None and total_tokens is not None:
        answer_tokens = max(0, total_tokens - prompt_tokens)
    if prompt_tokens is None and answer_tokens is not None and total_tokens is not None:
        prompt_tokens = max(0, total_tokens - answer_tokens)
    if prompt_tokens is None or answer_tokens is None:
        return None
    if total_tokens is None:
        total_tokens = prompt_tokens + answer_tokens
    completion_details = usage.get("completion_tokens_details")
    prompt_details = usage.get("prompt_tokens_details")
    details = {
        # 这些明细先透传到 usage_details，后续成本治理步骤再讨论如何使用它们。
        "reasoning_tokens": _read_usage_int(completion_details, "reasoning_tokens") if isinstance(completion_details, dict) else None,
        "cached_tokens": _read_usage_int(prompt_details, "cached_tokens") if isinstance(prompt_details, dict) else None,
        "prompt_cache_hit_tokens": _read_usage_int(usage, "prompt_cache_hit_tokens"),
        "prompt_cache_miss_tokens": _read_usage_int(usage, "prompt_cache_miss_tokens"),
    }
    return TokenUsage(
        prompt_tokens=prompt_tokens,
        answer_tokens=answer_tokens,
        total_tokens=total_tokens,
        details={key: value for key, value in details.items() if value is not None},
    )


def read_price_per_1k(name: str, default: float) -> float:
    """读取每千 token 单价配置，配置无效时回到项目默认值。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def build_cost_summary(messages: list[dict[str, str]], answer: str, usage: TokenUsage | None = None) -> CostSummary:
    """根据模型 usage 或本地估算生成本轮公开成本摘要。"""

    estimated_prompt_tokens, context_chars = estimate_messages_tokens(messages)
    if usage is not None:
        # usage 是更可信的计量来源；本地估算只在平台不给数据时兜底。
        prompt_tokens = usage.prompt_tokens
        answer_tokens = usage.answer_tokens
        total_tokens = usage.total_tokens
        usage_details = usage.details
        token_source: Literal["model_usage", "local_estimate"] = "model_usage"
        pricing_note = "优先使用模型平台返回的 usage；金额仍按项目默认单价做趋势观察，真实账单以平台计费为准。"
    else:
        prompt_tokens = estimated_prompt_tokens
        answer_tokens = estimate_tokens(answer)
        total_tokens = prompt_tokens + answer_tokens
        usage_details = {}
        token_source = "local_estimate"
        pricing_note = "模型平台未返回 usage 时使用本地 estimate_tokens 兜底；真实账单以平台计费为准。"

    input_price = read_price_per_1k("AGENT_GUHE_INPUT_CNY_PER_1K", DEFAULT_INPUT_CNY_PER_1K)
    output_price = read_price_per_1k("AGENT_GUHE_OUTPUT_CNY_PER_1K", DEFAULT_OUTPUT_CNY_PER_1K)
    # 项目重点：很多模型平台输入 token 和输出 token 单价不同，所以这里分开计算。
    input_cost = prompt_tokens / 1000 * input_price
    output_cost = answer_tokens / 1000 * output_price
    return CostSummary(
        prompt_tokens=prompt_tokens,
        answer_tokens=answer_tokens,
        total_tokens=total_tokens,
        token_source=token_source,
        usage_details=usage_details,
        estimated_input_cost_cny=round(input_cost, 6),
        estimated_output_cost_cny=round(output_cost, 6),
        estimated_total_cost_cny=round(input_cost + output_cost, 6),
        context_chars=context_chars,
        pricing_note=pricing_note,
    )
