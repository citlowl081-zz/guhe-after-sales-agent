"""Step 24：把 MCP 工具定义转换成项目工具契约。

本步的工具契约来自 MCP 目录中的 `MCPToolDefinition.to_tool_spec()`；
这里暴露 Agent 编排层需要的 `ToolSpec` 类型，MCP 目录继续负责资源和工具定义。
"""

from __future__ import annotations

from api.schemas import ToolSpec
