"""模型客户端层，封装 OpenAI-compatible 聊天模型调用和错误边界。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import os

import httpx

from api.schemas import ChatModelResult
from config.settings import api_key_is_missing, load_guhe_env
from cost.observer import parse_model_usage


def call_chat_model(
    messages: list[dict[str, str]],
    *,
    http_client: httpx.Client | None = None,
    api_key: str | None = None,
    base_url: str | None = None,
    model: str | None = None,
) -> ChatModelResult:
    """调用聊天模型，并保留平台 usage 供成本观察模块使用。"""

    load_guhe_env()
    resolved_api_key = api_key or os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(resolved_api_key):
        # 没有模型 usage 时也要继续返回 ChatModelResult，让成本模块能走本地估算路径。
        return ChatModelResult(
            answer="统一运行配置未设置有效的 AGENT_OPENAI_API_KEY。当前版本会用本地估算观察 Prompt token 趋势。"
        )

    resolved_base_url = (base_url or os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1")).rstrip("/")
    resolved_model = model or os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    # Step 07 开始保留 usage；但 usage 解析仍交给 cost/observer.py，不塞进 HTTP 调用层。
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
    return ChatModelResult(answer=payload["choices"][0]["message"]["content"], usage=parse_model_usage(payload))
