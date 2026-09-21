"""Step 16 后端薄入口。

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

from agents.customer_service_agent import Step16Agent
from api.routes import create_router
from api.schemas import ChatRequest, KnowledgeChunk
from rag.index_cache import rebuild_knowledge_index, reset_index_and_cache
from rag.knowledge_base import load_knowledge_chunks
from rag.planning import pre_retrieval_plan


def create_app() -> FastAPI:
    """创建 FastAPI 应用并挂载 Step 16 路由。"""
    app = FastAPI(title="Step 16 Guhe Agent Index Cache")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.include_router(create_router(lambda: agent))
    return app


agent = Step16Agent()
app = create_app()


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None)
