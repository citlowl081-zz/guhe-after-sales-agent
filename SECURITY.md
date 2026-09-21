# 安全说明

## 密钥与本地配置

本仓库不包含任何真实密钥。所有需要自行填写的凭据只存在于本机、且已被 `.gitignore` 排除的两个文件里：

| 文件 | 用途 | 提交到仓库的对应模板 |
| --- | --- | --- |
| `.env` | Docker Compose 使用的 MySQL 数据库名、本地演示密码和服务令牌 | `.env.example` |
| `agent-steps/guhe.env` | 所有 Step 共用的模型平台 API Key、模型名与日志级别 | `agent-steps/guhe.env.example` |

第一次运行前，从对应的 `*.example` 复制一份并填入自己的值。模板里的值都是占位符，可以直接提交；真实文件不要复制到其他路径、不要通过聊天、截图或日志分享。

## 不要提交任何 Key

- 不要把 API Key、Token、密码、私钥或数据库导出写进代码、README、示例 JSON、测试数据或注释。
- 提交前用 `git status` 与 `git diff --cached` 检查暂存区，确认没有 `.env`、`guhe.env`、`HANDOFF.md`、`.venv/`、`node_modules/`、`__pycache__/`、`*.log`、`.workflow_checkpoints/` 等本地文件。
- 如果某个 Key 曾经被提交过（即使随后删除），把它视为已泄露：先到模型平台或对应服务吊销并重新生成，再清理历史。
- Agent 后端的共享日志会遮蔽能识别的 Key、Token、Authorization、Cookie、密码、手机号、地址和邮箱，但这不表示可以主动把敏感数据写进日志或 Prompt。演示时只使用仓库内置的演示账号与数据。

## 演示账号

`doc/运行手册.md` 中列出的 `zhangsan / 123456`、`admin / admin123456` 等账号仅用于本地 Docker 演示环境，由 `DataInitializer` 在启动时写入本地 MySQL，不对应任何真实用户或线上系统。

## 报告安全问题

如果你在本仓库中发现以下任一情况，请不要公开提 Issue，而是通过 GitHub 私密安全通告（Security → Report a vulnerability）或直接私信仓库维护者报告：

- 仓库历史中出现了真实密钥、Token 或个人隐私数据；
- Agent 可以被 Prompt 注入诱导执行未授权的退款、退货或越权查询；
- 商城网关或 Agent 接口存在绕过登录态、越权访问他人订单的方式；
- 依赖项存在已知高危漏洞。

报告时请附上复现步骤、涉及的 Step 或模块路径以及你认为的影响范围。收到后会先确认并吊销可能泄露的凭据，再修复并在修复说明中致谢（除非你希望匿名）。
