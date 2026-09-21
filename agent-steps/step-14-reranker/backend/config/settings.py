"""Step 14 运行配置。"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


BASE_DIR = Path(__file__).resolve().parents[1]
CAPABILITIES_PATH = BASE_DIR / "agent_capabilities.json"
KNOWLEDGE_PATH = BASE_DIR / "knowledge_chunks.json"
DEFAULT_GUHE_ENV_PATH = BASE_DIR.parents[1] / "guhe.env"
PLACEHOLDER_API_KEYS = {"", "你的模型平台 Key", "your-api-key", "YOUR_API_KEY"}
DEFAULT_EMBEDDING_BASE_URL = "https://api.siliconflow.cn/v1"
DEFAULT_EMBEDDING_MODEL = "Qwen/Qwen3-Embedding-4B"
CANDIDATE_K = 4
FINAL_TOP_K = 2
RETRIEVAL_SCORE_THRESHOLD = 0.2
# 初召回恢复向量分数后，低置信线要挡住知识库外问题的弱相关候选。
LOW_CONFIDENCE_THRESHOLD = 0.5
RERANK_MODEL_DEFAULT = "Qwen/Qwen3-Reranker-8B"
RERANK_INSTRUCTION_DEFAULT = (
    "Given a customer-service query for GuHe E-commerce, rank passages that directly support "
    "the answer higher. Prefer current effective rules over expired campaign notes, and prefer "
    "passages that contain the exact promotion, refund, shipping, or product constraints needed "
    "to answer the user."
)
RERANK_TIMEOUT_SECONDS = 10.0
NORMALIZATION_RULES: list[tuple[str, str, str]] = [
    ("那个", "", "去掉缺少上下文的指代词“那个”"),
    ("这个", "", "去掉缺少上下文的指代词“这个”"),
    ("那款", "", "去掉缺少上下文的指代词“那款”"),
    ("这款", "", "去掉缺少上下文的指代词“这款”"),
    ("耳麦", "耳机", "把用户口语“耳麦”对齐为知识库常用词“耳机”"),
    ("叠券", "叠加 优惠券", "把用户口语“叠券”展开为“叠加 优惠券”"),
    ("会员券", "优惠券", "把“会员券”归一到知识库里的“优惠券”"),
    ("能叠吗", "能否 叠加 优惠券", "把省略问法“能叠吗”展开为优惠叠加问题"),
    ("促销", "活动", "把“促销”归一到活动规则用词"),
]


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


def env_flag_enabled(name: str, default: bool = False) -> bool:
    """读取布尔环境变量开关。"""

    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def api_key_is_missing(api_key: str | None) -> bool:
    """识别空 Key 和项目模板里的占位 Key。"""

    return api_key is None or api_key.strip() in PLACEHOLDER_API_KEYS
