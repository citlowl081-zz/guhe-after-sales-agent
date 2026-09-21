"""Step 14 聊天模型调用封装。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

import os

import httpx

from config.settings import api_key_is_missing, load_guhe_env


def call_chat_model(messages: list[dict[str, str]]) -> str:
    """调用 OpenAI 兼容聊天接口；缺少 Key 时返回项目提示。"""

    load_guhe_env()
    if os.getenv("AGENT_GUHE_DISABLE_LLM") == "1":
        return "项目测试已关闭真实模型调用。当前版本已经完成 reranker 重排链路。"
    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(api_key):
        return "统一运行配置未设置有效的 AGENT_OPENAI_API_KEY。当前版本已经完成 reranker 重排链路。"

    base_url = os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")
    log_model_input(model=model, messages=messages, prompt_source=__file__)
    response = httpx.post(
        f"{base_url}/chat/completions",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
        json={"model": model, "messages": messages},
        timeout=30,
    )
    response.raise_for_status()
    payload = response.json()
    log_model_output(model=model, content=payload["choices"][0]["message"]["content"])
    return payload["choices"][0]["message"]["content"]
