"""Step 10 成本观察工具。"""

from __future__ import annotations

import os

from api.schemas import CostSummary
from config.settings import DEFAULT_INPUT_CNY_PER_1K, DEFAULT_OUTPUT_CNY_PER_1K


def estimate_tokens(text: str) -> int:
    """用简单字符规则估算 token 数。"""

    ascii_chars = sum(1 for char in text if ord(char) < 128)
    non_ascii_chars = len(text) - ascii_chars
    return max(1, ascii_chars // 4 + non_ascii_chars // 2)


def estimate_messages_tokens(messages: list[dict[str, str]]) -> tuple[int, int]:
    """估算 chat messages 的 token 数和原始字符数。"""

    text = "\n".join(f"{message['role']}:{message['content']}" for message in messages)
    return estimate_tokens(text), len(text)


def read_price_per_1k(name: str, default: float) -> float:
    """读取每千 token 单价，非法值回退到项目默认值。"""

    raw_value = os.getenv(name)
    if raw_value is None:
        return default
    try:
        return float(raw_value)
    except ValueError:
        return default


def build_cost_summary(messages: list[dict[str, str]], answer: str) -> CostSummary:
    """根据 Prompt 和回答估算本轮成本趋势。"""

    prompt_tokens, context_chars = estimate_messages_tokens(messages)
    answer_tokens = estimate_tokens(answer)
    input_price = read_price_per_1k("AGENT_GUHE_INPUT_CNY_PER_1K", DEFAULT_INPUT_CNY_PER_1K)
    output_price = read_price_per_1k("AGENT_GUHE_OUTPUT_CNY_PER_1K", DEFAULT_OUTPUT_CNY_PER_1K)
    input_cost = prompt_tokens / 1000 * input_price
    output_cost = answer_tokens / 1000 * output_price
    return CostSummary(
        prompt_tokens=prompt_tokens,
        answer_tokens=answer_tokens,
        total_tokens=prompt_tokens + answer_tokens,
        estimated_input_cost_cny=round(input_cost, 6),
        estimated_output_cost_cny=round(output_cost, 6),
        estimated_total_cost_cny=round(input_cost + output_cost, 6),
        context_chars=context_chars,
        pricing_note="使用项目默认单价做趋势观察，真实账单以模型平台计费为准。",
    )
