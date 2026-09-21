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

from agents.customer_service_agent import Step07Agent, classify_intent, first_matched_keywords
from api.routes import create_router
from api.schemas import ChatModelResult, ChatRequest, ChatResponse, CostSummary, Intent, IntentResult, PromptFragment, ReasoningView, TokenUsage
from config.settings import load_agent_capabilities, load_guhe_env
from cost.observer import (
    build_cost_summary,
    estimate_messages_tokens,
    estimate_tokens,
    parse_model_usage,
    read_price_per_1k,
)
from models.llm_client import call_chat_model
from prompts.loader import load_prompt_registry, render_prompt_template, select_prompt_fragments


agent = Step07Agent()


def create_app() -> FastAPI:
    """组装 Step 07 FastAPI 应用，并挂载成本观察路由。"""

    app = FastAPI(title="Step 07 Guhe Agent Token Cost Observation")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    # 项目重点：成本观察已经从 Agent 编排中拆出，入口只负责装配当前版本服务。
    app.include_router(create_router(lambda: agent))
    return app


app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None)
