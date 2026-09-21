"""Step 12 运行配置。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
CAPABILITIES_PATH = BASE_DIR / "agent_capabilities.json"
KNOWLEDGE_DIR = BASE_DIR / "knowledge"
DEFAULT_GUHE_ENV_PATH = BASE_DIR.parents[1] / "guhe.env"
PLACEHOLDER_API_KEYS = {"", "你的模型平台 Key", "your-api-key", "YOUR_API_KEY"}
DEFAULT_EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
DEFAULT_INPUT_CNY_PER_1K = 0.001
DEFAULT_OUTPUT_CNY_PER_1K = 0.002
QUALITY_CASES_PATH = BASE_DIR / "rag_quality_cases.json"
CHUNK_SIZE = 420
CHUNK_OVERLAP = 80
TOP_K = 2
RETRIEVAL_SCORE_THRESHOLD = 0.2
# 低置信线看的是最高分命中是否足以支撑回答，不是候选 chunk 的入场线。
# 它来自本步固定问题集的正例、反例和边界样本分布；换模型或知识库后要重跑校准。
LOW_CONFIDENCE_THRESHOLD = 0.68


def load_agent_capabilities() -> dict[str, Any]:
    """读取调试后台能力声明。"""

    with CAPABILITIES_PATH.open(encoding="utf-8") as file:
        return json.load(file)


def load_guhe_env() -> Path | None:
    """把统一项目环境文件加载到进程环境变量中。"""

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
    """识别空 Key 和项目模板里的占位 Key。"""

    return api_key is None or api_key.strip() in PLACEHOLDER_API_KEYS
