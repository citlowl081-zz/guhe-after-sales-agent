"""Step 18：真实大模型适配器。

Tool Calling 和稳定知识 RAG 都使用同一份项目模型配置，但它们是两个不同职责：
- Tool Calling 需要 LangChain ChatModel，让模型生成 tool call。
- 稳定知识 RAG 只需要把已命中的引用材料组织成客服回答。
"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_model_input, log_model_output

from dataclasses import dataclass
import os

import httpx
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_openai import ChatOpenAI

from config.settings import api_key_is_missing, load_guhe_env


DEFAULT_CHAT_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_CHAT_MODEL = "Qwen/Qwen3-8B"
MODEL_CONFIG_MISSING_ANSWER = (
    "统一运行配置未设置有效的 AGENT_OPENAI_API_KEY。当前版本已经完成 RAG 检索和 LangChain Tool Calling 分流，"
    "但不能把配置缺失伪装成模型回答。"
)
STABLE_RAG_MODEL_UNAVAILABLE_ANSWER = (
    "模型服务暂时不可用，我先不编写没有模型核验的规则结论。你可以查看本轮引用材料，"
    "确认活动或售后规则后再继续。"
)


@dataclass(frozen=True)
class ChatModelConfig:
    """项目统一模型配置，供 Tool Calling 和稳定 RAG 两个适配器复用。"""

    api_key: str
    base_url: str
    model: str


@dataclass(frozen=True)
class StableRagAnswerResult:
    """稳定知识 RAG 的模型回答结果，供调试后台展示真实来源。"""

    answer: str
    used_model: bool
    model_name: str | None = None
    fallback_reason: str | None = None
    source: str = "stable_rag_chat_model"

    def model_dump(self) -> dict[str, object]:
        return {
            "used_model": self.used_model,
            "model_name": self.model_name,
            "fallback_reason": self.fallback_reason,
            "source": self.source,
        }


def load_chat_model_config() -> ChatModelConfig | None:
    """读取聊天模型配置；配置缺失时返回 None，由具体适配器决定边界行为。"""
    load_guhe_env()
    api_key = os.getenv("AGENT_OPENAI_API_KEY")
    if api_key_is_missing(api_key):
        return None
    return ChatModelConfig(
        api_key=api_key,
        base_url=os.getenv("AGENT_OPENAI_BASE_URL", DEFAULT_CHAT_BASE_URL).rstrip("/"),
        model=os.getenv("AGENT_OPENAI_MODEL", DEFAULT_CHAT_MODEL),
    )


def require_chat_model_config(purpose: str) -> ChatModelConfig:
    """Tool Calling 这类必须真实模型参与的路径，缺配置时直接报错。"""
    config = load_chat_model_config()
    if config is None:
        raise RuntimeError(
            f"AGENT_OPENAI_API_KEY 未配置，无法调用真实大模型进行 {purpose}。"
            "请在 agent-steps/guhe.env 中配置真实模型 Key。"
        )
    return config


def create_tool_calling_model() -> BaseChatModel:
    """创建真实 ChatModel，交给 LangChain 根据工具描述生成 tool call。"""
    config = require_chat_model_config("LangChain Tool Calling")
    return ChatOpenAI(
        model=config.model,
        api_key=config.api_key,
        base_url=config.base_url,
        temperature=0.1,
    )


def generate_stable_rag_answer(messages: list[dict[str, str]]) -> StableRagAnswerResult:
    """调用 OpenAI 兼容聊天接口，把稳定知识命中组织成客服回答。"""
    config = load_chat_model_config()
    if config is None:
        return StableRagAnswerResult(
            answer=MODEL_CONFIG_MISSING_ANSWER,
            used_model=False,
            fallback_reason="model_config_missing",
            source="stable_rag_model_boundary",
        )

    log_model_input(model=config.model, messages=messages, prompt_source=__file__)
    try:
        response = httpx.post(
            f"{config.base_url}/chat/completions",
            headers={"Authorization": f"Bearer {config.api_key}", "Content-Type": "application/json"},
            json={"model": config.model, "messages": messages},
            timeout=12,
        )
        response.raise_for_status()
        payload = response.json()
        answer = str(payload["choices"][0]["message"]["content"]).strip()
        log_model_output(model=config.model, content=answer)
        if not answer:
            return StableRagAnswerResult(
                answer=STABLE_RAG_MODEL_UNAVAILABLE_ANSWER,
                used_model=False,
                model_name=config.model,
                fallback_reason="empty_model_answer",
                source="stable_rag_model_boundary",
            )
        return StableRagAnswerResult(answer=answer, used_model=True, model_name=config.model)
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError) as exc:
        # Step 18 讲 Tool Calling，不提前引入 Step 21 的错误降级策略。
        return StableRagAnswerResult(
            answer=STABLE_RAG_MODEL_UNAVAILABLE_ANSWER,
            used_model=False,
            model_name=config.model,
            fallback_reason=exc.__class__.__name__,
            source="stable_rag_model_boundary",
        )
