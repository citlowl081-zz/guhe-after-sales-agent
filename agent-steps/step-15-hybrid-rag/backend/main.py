"""Step 15 后端薄入口。

具体能力已经拆进业务模块；这里只做 FastAPI 装配，并显式导出测试会用到的项目符号。
"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from guhe_runtime.guhe_logging import configure_guhe_logging, guhe_access_log_enabled
configure_guhe_logging(Path(__file__).resolve().parent)

import uvicorn
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from agents.customer_service_agent import Step15Agent
from api.routes import create_router
from api.schemas import ChatRequest
from rag.planning import pre_retrieval_plan


def create_app() -> FastAPI:
    """创建 FastAPI 应用并挂载 Step 15 路由。"""
    app = FastAPI(title="Step 15 Guhe Agent Hybrid RAG")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_router(lambda: agent))
    return app


agent = Step15Agent()
app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None)
