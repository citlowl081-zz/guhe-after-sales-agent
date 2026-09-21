#!/usr/bin/env python3
"""保护古河项目的 Git 写操作、依赖安装和本地密钥。

Cursor 会把即将执行的 Shell 命令以 JSON 写入标准输入。本 Hook 只做开发过程
保护，不参与古河商城 Agent 的业务运行。
"""

from __future__ import annotations

import json
import re
import shlex
import sys


GIT_WRITE_COMMANDS = {
    "add",
    "branch",
    "checkout",
    "cherry-pick",
    "clean",
    "clone",
    "commit",
    "config",
    "fetch",
    "gc",
    "init",
    "maintenance",
    "merge",
    "mv",
    "notes",
    "prune",
    "pull",
    "push",
    "rebase",
    "reflog",
    "remote",
    "replace",
    "reset",
    "restore",
    "revert",
    "rm",
    "stash",
    "submodule",
    "switch",
    "tag",
    "update-ref",
    "worktree",
}
GIT_OPTIONS_WITH_VALUE = {
    "-C",
    "-c",
    "--config-env",
    "--git-dir",
    "--namespace",
    "--super-prefix",
    "--work-tree",
}
INSTALL_PATTERN = re.compile(
    r"(?:\bpip(?:3)?\s+install\b|\bpython(?:3(?:\.\d+)?)?\s+-m\s+pip\s+install\b|"
    r"\bnpm\s+(?:install|ci)\b|\bbrew\s+install\b|\bdocker\s+(?:pull|build)\b|"
    r"\bdocker\s+compose\b.*\b(?:build|up)\b)",
    flags=re.IGNORECASE,
)
LOCAL_ENV_PATTERN = re.compile(r"(?<![\w.])\.env(?![\w.])")
ENV_EXAMPLE_PATTERN = re.compile(r"(?<![\w.])\.env\.example(?![\w.])")


def requests_git_write(command: str) -> bool:
    """识别 Git 子命令，并覆盖 `git -C <path> add` 等全局选项写法。"""

    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars=";&|")
        lexer.whitespace_split = True
        lexer.commenters = ""
        tokens = list(lexer)
    except ValueError:
        # 命令无法可靠解析时宁可请求确认，避免格式异常绕过写操作保护。
        return bool(re.search(r"(?:^|[/\s])git(?:\s|$)", command))

    for index, token in enumerate(tokens):
        if token.rsplit("/", 1)[-1].lower() != "git":
            continue
        cursor = index + 1
        while cursor < len(tokens):
            current = tokens[cursor]
            if current in {"&&", "||", ";", "|", "&"}:
                break
            if not current.startswith("-"):
                return current.lower() in GIT_WRITE_COMMANDS
            option_name = current.split("=", 1)[0]
            if option_name in GIT_OPTIONS_WITH_VALUE and "=" not in current:
                cursor += 2
            else:
                cursor += 1
    return False


def emit(permission: str, user_message: str = "", agent_message: str = "") -> None:
    payload: dict[str, str] = {"permission": permission}
    if user_message:
        payload["user_message"] = user_message
    if agent_message:
        payload["agent_message"] = agent_message
    print(json.dumps(payload, ensure_ascii=False))


def main() -> int:
    try:
        event = json.load(sys.stdin)
    except (json.JSONDecodeError, TypeError):
        # hooks.json 使用 failClosed；输入损坏时明确拒绝，避免绕过安全检查。
        emit("deny", "Shell 安全 Hook 无法解析输入，已阻止命令。")
        return 0

    command = str(event.get("command") or "")
    without_examples = ENV_EXAMPLE_PATTERN.sub("", command)

    if LOCAL_ENV_PATTERN.search(without_examples):
        emit(
            "deny",
            "真实 .env 属于本地密钥文件，项目规则禁止通过 Shell 读取、打印或提交。",
            "请只操作可公开的 .env.example；不要读取真实 .env。",
        )
        return 0

    if requests_git_write(command):
        emit(
            "ask",
            "古河项目规定 Git 写操作必须先获得你的明确同意。",
            "请确认用户已经授权本次具体 Git 操作。",
        )
        return 0

    if INSTALL_PATTERN.search(command):
        emit(
            "ask",
            "该命令会安装依赖或启动构建，请先确认安装计划。",
            "先展示将安装或启动的内容，再请求用户批准。",
        )
        return 0

    emit("allow")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
