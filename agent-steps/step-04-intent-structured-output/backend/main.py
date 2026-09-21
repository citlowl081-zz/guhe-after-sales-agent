"""步骤快照薄入口：只负责导入 FastAPI app 并启动当前步后端。"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from guhe_runtime.guhe_logging import configure_guhe_logging, guhe_access_log_enabled
configure_guhe_logging(Path(__file__).resolve().parent)

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.customer_service_agent import Step04Agent, build_answer, classify_intent, first_matched_keywords, plan_intent_by_rules
from api.routes import create_router
from api.schemas import ChatRequest, ChatResponse, Intent, IntentResult, IntentSource, ReasoningView
from config.settings import load_agent_capabilities, load_guhe_env
from models.classifier_client import build_classifier_messages, classify_intent_with_model, parse_classifier_json


agent = Step04Agent()


def create_app() -> FastAPI:
    """组装 Step 04 FastAPI 应用，并挂载结构化意图路由。"""

    app = FastAPI(title="Step 04 Guhe Agent Structured Intent")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 项目重点：从这一步开始，入口只装配 API，意图识别留在 Agent 和模型客户端模块。
    app.include_router(create_router(lambda: agent))
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None)
