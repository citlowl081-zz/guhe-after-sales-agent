---
name: guhe-project-assistant
description: 古河商城售后协同 Agent 的项目助手。用户在本项目中提到古河商城、某个 Step、启动、运行、调试台、README、验收、排错或下一步时使用。负责校验目录与环境、启动电商后端 / 某个 Step 的 Agent 后端 / 调试台、生成运行卡片、定位某能力在哪些 Step 展开、逐 Step 验收。
---

# 古河项目助手

## 开工顺序

1. 确认当前目录是项目根：应同时存在 `agent-steps/`、`ecommerce-backend/`、`frontend/`、`docker-compose.infra.yml`、`requirements.txt`。
2. 读 `doc/运行手册.md`（运行权威）和 `agent-steps/README.md`（Step 索引），再读目标 Step 的 README。
3. 检查运行前提：Docker、`.venv/bin/python`（3.13）、Node/npm、`agent-steps/guhe.env` 是否存在。缺少时先列安装计划，获批后再装。
4. 按下面的运行卡片启动，并用真实请求验证。

## 运行卡片模板

```text
Step NN · <名称>
- 电商后端：docker compose -f docker-compose.infra.yml up -d --build → http://localhost:8081/actuator/health
- Agent 后端：cd agent-steps/step-NN-*/backend && ../../../.venv/bin/python main.py → http://localhost:8000/health
- 调试台：cd frontend && npm run dev                                   → http://localhost:5173
- 模型配置：agent-steps/guhe.env（AGENT_OPENAI_API_KEY 等）
- 验证问题：<该 Step README 里的示例问题>
- 观察信号：<该 Step agent_capabilities.json 声明的能力字段>
```

Step 01 是本地脚本，直接 `../../.venv/bin/python main.py`；Step 42–45 复用 Step 41 后端；Step 46 无后端。

## 能力线定位

| 能力线 | 起始 Step | 展开 Step |
|---|---|---|
| Prompt / 结构化输出 | 04 | 05–07 |
| RAG | 08 | 09–16、22 |
| 业务事实 / Tool | 17 | 18–24 |
| 规划与高风险边界 | 25 | 26 |
| Workflow / HITL | 27 | 28–31 |
| 记忆与上下文 | 32 | 33–35 |
| 安全 / Trace / Eval / 成本 | 36 | 37–40 |
| 综合演练与交付 | 41 | 42–46 |

## Step 验收清单

- [ ] `backend/main.py` 可启动，`/health` 与 `/capabilities` 返回正常。
- [ ] 至少一次真实 `/chat` 请求，响应字段与 `agent_capabilities.json` 声明一致。
- [ ] README 含：本步目标、模块划分、核心链路、启动命令、验证问题、已知边界。
- [ ] 禁词检查通过：不含其他品牌名、外部教程用语。
- [ ] 未修改其他 Step 目录。

## 排错口径

- 401/403：Key 无效或无权限；404：模型名或 base_url 错；429：拥堵，稍后重试；网络错误：检查代理与 DNS。
- 8081 打不开：`docker compose -f docker-compose.infra.yml ps` 看 `ecommerce-service` 是否 healthy。
- 5173 打不开：确认 `frontend/node_modules` 已安装且 `VITE_AGENT_BASE_URL` / `VITE_ECOMMERCE_BASE_URL` 指向正确的后端。
- 不读取、不打印真实 `.env` / `guhe.env`；需要用户贴日志时提醒先打码。
