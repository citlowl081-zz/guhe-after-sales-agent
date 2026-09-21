"""FastAPI 路由层，只负责接入 Agent、Trace、Eval 或 Feedback。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *
from config.settings import load_agent_capabilities
from agents.customer_service_agent import Step37Agent
from observability.trace import trace_store

app = FastAPI(title="Step 37 Guhe Agent Trace Observability")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Step37Agent()

@app.get("/health")
def health() -> dict[str, str]:
    """提供步骤快照健康检查。"""
    return {"status": "ok", "step": "37"}

@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """返回当前步程能力清单。"""
    return load_agent_capabilities()

@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """处理一次聊天请求。"""
    return agent.chat(request)

@app.post("/chat/resume")
def chat_resume(request: ChatResumeRequest) -> ChatResumeResponse:
    """恢复一个暂停在 HITL 节点的售后 workflow。"""
    return agent.resume(request)

@app.get("/sessions/{session_id}/trace")
def session_trace(session_id: str) -> list[dict[str, Any]]:
    """返回指定会话的公开 Trace 事件。"""
    return [event.model_dump() for event in trace_store.list(session_id)]
