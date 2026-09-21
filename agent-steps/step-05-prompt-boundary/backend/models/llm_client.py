"""模型客户端层，封装 OpenAI-compatible 聊天模型调用和错误边界。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import os

import httpx

from config.settings import api_key_is_missing, load_guhe_env


def call_chat_model(
    messages: list[dict[str, str]],
    *,
    http_client: httpx.Client | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> str:
    """调用真实模型生成被 Prompt 边界和全量规则上下文约束后的客服回答。"""

    load_guhe_env()
    resolved_api_key = api_key or os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(resolved_api_key):
        # 配置缺失时只返回诊断提示，不把 Prompt 已构造当成业务已经可用。
        return "统一运行配置未设置有效的 AGENT_OPENAI_API_KEY。当前版本已构造 Prompt 边界和全量规则上下文，但还不能调用真实模型生成回复。"

    resolved_base_url = (base_url or os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")).rstrip("/")
    resolved_model = model or os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    # 模型客户端消费 Prompt 模块产出的 messages，但不判断哪些规则是 current 或 legacy。
    request_kwargs = {
        "headers": {"Authorization": f"Bearer {resolved_api_key}", "Content-Type": "application/json"},
        "json": {"model": resolved_model, "messages": messages},
    }
    log_model_input(model=resolved_model, messages=request_kwargs["json"]["messages"], prompt_source=__file__)
    if http_client is not None:
        response = http_client.post(f"{resolved_base_url}/chat/completions", **request_kwargs)
    else:
        response = httpx.post(f"{resolved_base_url}/chat/completions", **request_kwargs, timeout=30)
    response.raise_for_status()
    payload = response.json()
    log_model_output(model=resolved_model, content=payload["choices"][0]["message"]["content"])
    return payload["choices"][0]["message"]["content"]
