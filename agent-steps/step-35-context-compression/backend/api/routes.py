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
from agents.customer_service_agent import Step35Agent

app = FastAPI(title="Step 35 Guhe Agent Context Compression")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Step35Agent()

@app.get("/health")
def health() -> dict[str, str]:
    """提供步骤快照健康检查。"""
    return {"status": "ok", "step": "35"}

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
    """恢复暂停中的高风险 workflow。"""
    return agent.resume(request)
