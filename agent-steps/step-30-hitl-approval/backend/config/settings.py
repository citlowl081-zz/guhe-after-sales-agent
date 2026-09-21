"""步骤快照配置读取。这里只处理能力清单、guhe.env 和业务后端地址。"""

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

CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "agent_capabilities.json"

DEFAULT_GUHE_ENV_PATH = Path(__file__).resolve().parents[3] / "guhe.env"

DEFAULT_ECOMMERCE_BASE_URL = "http://127.0.0.1:8081"

TODAY = date(2026, 6, 7)

def load_guhe_env() -> Path | None:
    """读取项目根目录的 guhe.env，让快照可复用同一份运行配置。"""
    env_path = Path(os.getenv("AGENT_GUHE_ENV", str(DEFAULT_GUHE_ENV_PATH))).expanduser()
    if not env_path.exists():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))
    return env_path

def ecommerce_base_url() -> str:
    """解析古河业务后端地址，没有配置时使用本地默认地址。"""
    return os.getenv("ECOMMERCE_BASE_URL", os.getenv("AGENT_ECOMMERCE_BASE_URL", DEFAULT_ECOMMERCE_BASE_URL)).rstrip("/")

def load_agent_capabilities() -> dict[str, Any]:
    """读取本步能力清单，供前端和测试确认项目边界。"""
    with CAPABILITIES_PATH.open(encoding="utf-8") as file:
        return json.load(file)
