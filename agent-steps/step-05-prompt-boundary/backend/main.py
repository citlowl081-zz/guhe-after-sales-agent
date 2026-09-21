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

from agents.customer_service_agent import Step05Agent, classify_intent, first_matched_keywords
from api.routes import create_router
from api.schemas import ChatRequest, ChatResponse, ContextConflict, Intent, IntentResult, PolicyDocument, ReasoningView
from config.settings import load_agent_capabilities, load_guhe_env
from models.llm_client import call_chat_model
from prompts.loader import (
    CONFLICT_RULES,
    FULL_POLICY_DOCUMENTS,
    build_all_policy_context,
    build_full_context_messages,
    detect_context_conflicts,
    estimate_tokens,
)


agent = Step05Agent()


def create_app() -> FastAPI:
    """组装 Step 05 FastAPI 应用，并挂载 Prompt 边界路由。"""

    app = FastAPI(title="Step 05 Guhe Agent Prompt Boundary And Full Context")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 项目重点：main.py 只装配服务，Prompt 文档与拼装逻辑已经迁入 prompts/。
    app.include_router(create_router(lambda: agent))
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None)
