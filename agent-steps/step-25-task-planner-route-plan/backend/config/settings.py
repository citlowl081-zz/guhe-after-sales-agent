"""Step 25：项目配置和 capabilities 读取。运行路径从这里统一管理。"""

from __future__ import annotations

import json
import os
from datetime import date
from pathlib import Path
from typing import Any


CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "agent_capabilities.json"

DEFAULT_GUHE_ENV_PATH = Path(__file__).resolve().parents[3] / "guhe.env"

DEFAULT_ECOMMERCE_BASE_URL = "http://127.0.0.1:8081"

PLACEHOLDER_API_KEYS = {"", "你的模型平台 Key", "sk-your-openai-compatible-key", "your-api-key-here", "replace-me"}

def load_guhe_env() -> Path | None:
    """读取全程共享模型配置，让本步快照和生产版使用同一套 OpenAI-compatible 参数。"""

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

def _api_key_is_missing(api_key: str | None) -> bool:
    """判断模型 API Key 是否仍是占位值。"""
    return api_key is None or api_key.strip() in PLACEHOLDER_API_KEYS

def load_agent_capabilities() -> dict[str, Any]:
    """读取当前步程 capabilities 文件。"""
    with CAPABILITIES_PATH.open(encoding="utf-8") as file:
        return json.load(file)
