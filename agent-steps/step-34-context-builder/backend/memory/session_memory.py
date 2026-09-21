"""会话记忆层。只记录低风险、已验证、当前会话内的信息。"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from threading import RLock
from typing import Any, Literal, TypedDict

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from api.schemas import *
SESSION_MEMORIES: dict[str, dict[str, Any]] = {}
SESSION_MEMORY_OWNERS: dict[str, str] = {}
SESSION_MEMORY_LOCK = RLock()

MESSAGE_COUNT_BY_SESSION: dict[str, int] = {}

WORKFLOW_CHECKPOINTS: dict[tuple[str, str], dict[str, Any]] = {}

SUBMITTED_ACTIONS: dict[str, dict[str, Any]] = {}

def current_memory(session_id: str, runtime_user_id: str) -> dict[str, Any]:
    """读取当前用户的会话记忆，阻止 Context Builder 接收其他用户状态。"""
    with SESSION_MEMORY_LOCK:
        memory = SESSION_MEMORIES.get(session_id)
        if memory is None or SESSION_MEMORY_OWNERS.get(session_id) != runtime_user_id:
            # 先完成属主与 Memory 的原子绑定，再把候选交给 Context Builder。
            memory = {"last_order_id": None, "recent_intent": None}
            SESSION_MEMORIES[session_id] = memory
            SESSION_MEMORY_OWNERS[session_id] = runtime_user_id
        return memory
