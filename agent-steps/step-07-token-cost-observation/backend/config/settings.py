"""运行配置层，集中读取项目环境变量、能力声明和模型连接参数。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BACKEND_DIR = Path(__file__).resolve().parents[1]
CAPABILITIES_PATH = BACKEND_DIR / "agent_capabilities.json"
# Prompt Registry 继续作为项目文件存在；Step 07 只在它之外增加成本观察。
PROMPT_REGISTRY_PATH = BACKEND_DIR / "prompt_registry.json"
# 项目所有 step 共享一份 guhe.env，避免每一步重复配置模型 Key。
DEFAULT_GUHE_ENV_PATH = BACKEND_DIR.parents[1] / "guhe.env"
PLACEHOLDER_API_KEYS = {"", "你的模型平台 Key", "your-api-key", "YOUR_API_KEY"}
# 默认价格只用于本地趋势观察，不替代模型平台真实账单。
DEFAULT_INPUT_CNY_PER_1K = 0.001
DEFAULT_OUTPUT_CNY_PER_1K = 0.002


def load_agent_capabilities() -> dict[str, Any]:
    """读取调试后台能力声明，不参与 Agent 主链路。"""

    with CAPABILITIES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def load_guhe_env() -> Path | None:
    """读取全程共享模型配置，避免每步复制环境变量说明。"""

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


def api_key_is_missing(api_key: str | None) -> bool:
    """判断模型 Key 是否缺失或仍是项目占位值。"""

    # 占位 Key 要当成未配置处理，不能让它一路打到模型平台才失败。
    return api_key is None or api_key.strip() in PLACEHOLDER_API_KEYS
