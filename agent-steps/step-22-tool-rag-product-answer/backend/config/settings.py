"""Step 22：配置与能力声明读取。项目代码从这里统一读取环境变量和 capabilities 文件。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


CAPABILITIES_PATH = Path(__file__).resolve().parents[1] / "agent_capabilities.json"

KNOWLEDGE_PATH = Path(__file__).resolve().parents[1] / "knowledge_chunks.json"

DEFAULT_GUHE_ENV_PATH = Path(__file__).resolve().parents[3] / "guhe.env"

DEFAULT_ECOMMERCE_BASE_URL = "http://127.0.0.1:8081"

def load_agent_capabilities() -> dict[str, Any]:
    """读取当前步程对外声明的能力开关。"""
    with CAPABILITIES_PATH.open(encoding="utf-8") as file:
        return json.load(file)

def load_guhe_env() -> Path | None:
    """加载项目级环境变量，方便每步快照独立运行。"""
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
