"""公开 Trace 事件规范化和存储层，不暴露 hidden CoT。"""

from __future__ import annotations

import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from guhe_runtime.guhe_logging import log_guhe_event

from api.schemas import *
from config.settings import TRACE_SCHEMA_VERSION

class TraceEventNormalizer:
    """把模块事件整理成公开 trace schema。

    Trace 只记录可展示的执行摘要：工具名、RAG 命中、workflow 状态、HITL 边界、
    Hooks 和成本路径。它不是 hidden CoT，也不保存系统提示词或原始隐私内容。
    """

    _CATEGORY_BY_EVENT = {
        "runtime_context_built": "runtime_context",
        "context_built": "context",
        "rag_pre_retrieved": "rag",
        "tool_started": "tool",
        "tool_finished": "tool",
        "workflow_completed": "workflow",
        "human_approval_required": "hitl",
        "hook_executed": "hook",
        "prompt_security_blocked": "prompt",
        "cost_recorded": "cost",
        "final_answer_generated": "answer",
    }
    _PHONE_PATTERN = re.compile(r"\b1[3-9]\d{9}\b")
    _ADDRESS_PATTERN = re.compile(r"(收货地址|地址)\s*[:：]\s*[^,，。;\n]+")
    _SECRET_PATTERN = re.compile(r"(?i)(api[_ -]?key|access[_ -]?token|secret|sk-[A-Za-z0-9_-]{8,})")

    @classmethod
    def normalize(cls, *, session_id: str, event_type: str, payload: dict[str, Any], step: int) -> TraceEvent:
        """清洗 Trace payload，避免手机号、地址、原始长文本等敏感信息进入公开日志。"""
        safe_payload = cls.sanitize(payload)
        category = cls._CATEGORY_BY_EVENT.get(event_type, "system")
        return TraceEvent(
            event_type=event_type,
            timestamp=datetime.now(timezone.utc),
            agent_mode="step-37-trace-observability",
            step=step,
            schema_version=TRACE_SCHEMA_VERSION,
            category=category,
            stage=cls.stage(event_type, safe_payload),
            name=event_type,
            status=cls.status(event_type, safe_payload),
            target=cls.target(event_type, safe_payload),
            ids={
                "session_id": session_id,
                "workflow_id": safe_payload.get("workflow_id"),
                "order_no": safe_payload.get("order_id"),
                "tool_call_id": safe_payload.get("tool_call_id"),
            },
            summary=cls.summary(event_type, safe_payload),
            signals=cls.signals(event_type, safe_payload),
            safety={
                "public_trace": True,
                "hidden_cot_exposed": False,
                "payload_sanitized": safe_payload != payload,
                "contains_sensitive_raw": False,
            },
            payload=safe_payload,
        )

    @classmethod
    def sanitize(cls, value: Any) -> Any:
        """递归清洗 Trace 字段，只保留调试需要的安全摘要。"""
        if isinstance(value, dict):
            return {key: cls.sanitize(item) for key, item in value.items() if key not in {"system_prompt", "hidden_reasoning"}}
        if isinstance(value, list):
            return [cls.sanitize(item) for item in value]
        if isinstance(value, str):
            text = cls._PHONE_PATTERN.sub("1**********", value)
            text = cls._ADDRESS_PATTERN.sub(r"\1：[已脱敏地址]", text)
            text = cls._SECRET_PATTERN.sub("[已脱敏密钥]", text)
            text = text.replace("hidden reasoning", "[受保护推理摘要]").replace("隐藏推理", "[受保护推理摘要]")
            return text.replace("系统提示词", "[受保护系统信息]")
        return value

    @staticmethod
    def stage(event_type: str, payload: dict[str, Any]) -> str:
        """从事件类型推断 Trace 阶段，帮助你按 Agent 执行链路阅读事件。"""
        if event_type.endswith("_started"):
            return "start"
        if event_type.endswith("_finished") or event_type.endswith("_completed"):
            return "finish"
        if event_type.startswith("rag_"):
            return "retrieval"
        if event_type.startswith("human_approval"):
            return "approval"
        if event_type == "cost_recorded":
            return "cost_summary"
        if event_type == "hook_executed":
            return str(payload.get("hook_type") or "hook")
        return "event"

    @staticmethod
    def status(event_type: str, payload: dict[str, Any]) -> str:
        """把事件转换成统一状态，便于前端观察台和 Eval 判断。"""
        if payload.get("status"):
            return str(payload["status"])
        if event_type.endswith("_started"):
            return "started"
        if event_type.endswith("_finished") or event_type.endswith("_completed"):
            return "success"
        if event_type == "human_approval_required":
            return "warning"
        return "recorded"

    @staticmethod
    def target(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """抽取事件目标，记录本步处理的是工具、RAG、workflow 还是回答。"""
        return {
            "type": payload.get("target_type") or ("tool" if event_type.startswith("tool_") else None),
            "name": payload.get("tool_name") or payload.get("workflow_type") or payload.get("target_name"),
        }

    @staticmethod
    def summary(event_type: str, payload: dict[str, Any]) -> dict[str, Any]:
        """生成 Trace 摘要，保留可解释信号而不是塞入完整上下文。"""
        keys = (
            "intent",
            "hit_count",
            "retrieval_stage",
            "tool_name",
            "risk_level",
            "needs_human_approval",
            "pending_action",
            "path_type",
            "tool_call_count",
            "degraded",
            "hook_type",
        )
        return {"event_type": event_type, **{key: payload[key] for key in keys if key in payload}}

    @staticmethod
    def signals(event_type: str, payload: dict[str, Any]) -> list[str]:
        """提取可用于评测的公开信号，避免 Eval 依赖 hidden reasoning。"""
        signals = [event_type]
        for key in ("tool_name", "workflow_type", "pending_action", "path_type", "hook_type"):
            if payload.get(key):
                signals.append(str(payload[key]))
        if payload.get("needs_human_approval") is True:
            signals.append("needs_human_approval=true")
        return signals

class TraceStore:
    def __init__(self) -> None:
        """初始化本步服务对象，把可替换依赖固定在实例上，便于测试和项目演进。"""
        self._events: dict[str, list[TraceEvent]] = {}

    def clear(self) -> None:
        """清空测试会话里的 Trace，保证项目用例之间互不串扰。"""
        self._events.clear()

    def add(self, session_id: str, event_type: str, payload: dict[str, Any]) -> TraceEvent:
        """追加 Trace 事件，并在写入前统一做安全清洗。"""
        events = self._events.setdefault(session_id, [])
        event = TraceEventNormalizer.normalize(session_id=session_id, event_type=event_type, payload=payload, step=len(events) + 1)
        events.append(event)
        log_guhe_event("TRACE_EVENT", event.summary, observe=event_type in {"runtime_context_built", "context_built", "tool_finished", "rag_pre_retrieved", "workflow_completed", "human_approval_required", "final_answer_generated"}, trace_event=event_type, step=event.step, category=event.category, status=event.status, payload=event.payload)
        return event

    def list(self, session_id: str) -> list[TraceEvent]:
        """按会话读取 Trace 事件，作为观察台和 Eval 的共同数据源。"""
        return list(self._events.get(session_id, []))

trace_store = TraceStore()
