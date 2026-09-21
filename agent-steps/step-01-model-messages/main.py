"""Step 01 本地脚本：只演示 messages 如何进入模型并取回 assistant message。

这一步故意不拆成 Web 后端，因为它是从零开始的第一步：先看懂 system、user、assistant 三类消息，下一步再长出 API 和 Agent 分层。
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx


DEFAULT_GUHE_ENV_PATH = Path(__file__).resolve().parents[1] / "guhe.env"
DEFAULT_USER_MESSAGE = "我在古河商城看到降噪耳机有活动，请问优惠能和会员券一起用吗？"
PLACEHOLDER_API_KEYS = {"", "你的模型平台 Key", "your-api-key", "YOUR_API_KEY"}


def load_guhe_env() -> Path | None:
    """读取全程共享配置，让这一步和后续项目使用同一套模型参数。"""

    env_path = Path(os.getenv("AGENT_GUHE_ENV", str(DEFAULT_GUHE_ENV_PATH))).expanduser()
    if not env_path.exists():
        return None

    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if key:
            os.environ.setdefault(key, value)
    return env_path


def build_messages(user_message: str = DEFAULT_USER_MESSAGE) -> list[dict[str, str]]:
    """把一次客服对话整理成模型能理解的 messages 列表。"""

    system_message = (
        "你是古河商城公司的客服 Agent。需要用客服语气回答用户问题，"
        "但不能承诺具体优惠、退款、发货、赔偿或人工处理结果。"
    )

    # 模型不是只接收一句散落的问题；system 先立身份和边界，user 再放用户真正说的话。
    return [
        {"role": "system", "content": system_message},
        {"role": "user", "content": user_message},
    ]


def _api_key_is_missing(api_key: str | None) -> bool:
    """识别空 Key 和项目模板占位 Key，避免本地脚本伪造模型成功。"""
    return api_key is None or api_key.strip() in PLACEHOLDER_API_KEYS


def call_chat_model(messages: list[dict[str, str]]) -> dict[str, Any]:
    """调用真实聊天模型，返回原始响应。

    这里不提供运行时 mock。没有 Key 就明确报错，因为本节要让你看清楚：
    assistant message 必须来自真实模型响应，而不是脚本偷偷补的一句假客服话术。
    """

    load_guhe_env()
    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if _api_key_is_missing(api_key):
        raise RuntimeError(
            "缺少有效的 AGENT_OPENAI_API_KEY。请在 agent-steps/guhe.env "
            "或 AGENT_GUHE_ENV 指定的配置文件中填写真实模型 Key。"
        )

    base_url = os.getenv("AGENT_OPENAI_BASE_URL", "https://api.siliconflow.cn/v1").rstrip("/")
    model = os.getenv("AGENT_OPENAI_MODEL", "Qwen/Qwen3-8B")

    try:
        response = httpx.post(
            f"{base_url}/chat/completions",
            headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
            json={"model": model, "messages": messages},
            timeout=30,
        )
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise RuntimeError("模型接口调用失败，请检查项目配置里的 Base URL、模型名和 API Key。") from exc

    try:
        payload = response.json()
    except ValueError as exc:
        raise RuntimeError("模型接口返回的不是合法 JSON，无法读取 assistant message。") from exc
    return payload


def extract_assistant_message(model_response: dict[str, Any]) -> str:
    """从模型响应里取出 assistant message。"""

    try:
        content = model_response["choices"][0]["message"]["content"]
    except (KeyError, IndexError, TypeError) as exc:
        raise RuntimeError("模型响应里没有 choices[0].message.content，无法取出 assistant message。") from exc

    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("assistant message 为空，不能当作有效客服回复。")
    return content


def main() -> int:
    """运行 Step 01 本地模型调用流程，并把 assistant message 打印到终端。"""
    messages = build_messages()

    try:
        model_response = call_chat_model(messages)
        assistant_message = extract_assistant_message(model_response)
    except RuntimeError as exc:
        print(f"模型调用未完成：{exc}")
        return 1

    print("古河商城客服 Agent 本地模型回复：")
    print(assistant_message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
