"""Step 10 运行配置。"""

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
DEFAULT_INPUT_CNY_PER_1K = 0.001
DEFAULT_OUTPUT_CNY_PER_1K = 0.002
# Step 10 开始做向量召回，chunk 要尽量保留一条规则的条件、结论和例外口径。
# 这不是 embedding 模型的固定要求，换知识库或模型后要重新验证检索颗粒度。
CHUNK_SIZE = 420
CHUNK_OVERLAP = 80
TOP_K = 2
# 这个阈值来自本步正例、负例和边界样本的分数分布：先挡掉明显无关片段，
# 再交给 top_k 收窄上下文。换模型或知识库后要重新校准，不能把 0.30 当通用标准。
# 低于门槛的候选不能生成 citation；“向量库总能排出前几名”不等于真的找到了依据。
SCORE_THRESHOLD = 0.30


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
