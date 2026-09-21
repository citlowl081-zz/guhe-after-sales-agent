"""FastAPI 路由层。路由只负责调用 Agent，并返回项目契约响应。"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from api.schemas import *
from config.settings import load_agent_capabilities
from agents.customer_service_agent import Step33Agent

app = FastAPI(title="Step 33 Guhe Agent Runtime Context")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

agent = Step33Agent()

@app.get("/capabilities")
def capabilities() -> dict[str, Any]:
    """返回当前步程开放和关闭的能力边界。"""
    return load_agent_capabilities()

@app.get("/health")
def health() -> dict[str, str]:
    """提供步骤快照的最小健康检查。"""
    return {"status": "ok", "step": "33"}

@app.post("/chat")
def chat(request: ChatRequest) -> ChatResponse:
    """处理一次 /chat 请求，并返回项目约定的公开响应结构。"""
    return agent.chat(request)

@app.post("/chat/resume")
def chat_resume(request: ChatResumeRequest) -> ChatResumeResponse:
    """恢复暂停中的高风险 workflow。"""
    return agent.resume(request)
