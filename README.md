# 古河商城售后协同 Agent

面向古河商城售后场景的客服 Agent，按 46 个 Step 从最小的模型调用逐步长成具备 Prompt 治理、RAG、Tool、Hooks、MCP、Workflow、HITL、记忆与上下文、安全防护、Trace、Eval 和成本治理能力的 Agent 后端。

## 目录

| 路径 | 说明 |
| --- | --- |
| `agent-steps/` | 46 个 Step 的代码快照与索引（`agent-steps/README.md`） |
| `ecommerce-backend/` | 古河商城业务后端（Spring Boot，Docker 启动，端口 8081） |
| `frontend/` | Agent 调试台（Vite，端口 5173） |
| `doc/` | 运行手册、入门指引、初始化数据说明 |
| `docker-compose.infra.yml` | MySQL + 电商后端（+ 可选 Chroma） |
| `requirements.txt` | Agent 后端 Python 依赖 |

## 快速开始

1. 阅读 `doc/古河商城 Agent 项目入门指引.md`，再按 `doc/运行手册.md` 准备 Docker、Python 3.13 `.venv` 与 Node/npm。
2. 复制 `agent-steps/guhe.env.example` 为 `agent-steps/guhe.env`，填入模型 Key。
3. `docker compose -f docker-compose.infra.yml up -d --build` 启动电商后端。
4. 进入某个 Step 的 `backend/` 运行 `python main.py`（端口 8000），`cd frontend && npm run dev` 打开调试台。

## 约定

- 真实密钥只放本地 `.env` 与 `agent-steps/guhe.env`，仓库只提交 `*.example`。
- 项目规则、Skill 与 Shell 安全 Hook 位于 `.cursor/`。
