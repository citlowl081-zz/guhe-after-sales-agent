"""Prompt Injection 和隐私清洗层，隔离外部脏指令。"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any, Literal

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *
from rag.knowledge import *
from tools.runtime_context import *
from tools.planning import classify_intent, extract_order_id
from integrations.ecommerce_client import order_fact_from_ecommerce

def scan_categories(text: str) -> list[str]:
    """识别外部文本中的脏指令类别，为后续消毒和 Trace 提供结构化信号。"""
    categories: list[str] = []
    prompt_terms = ["忽略以上", "忽略系统", "不要遵守", "覆盖规则", "按我的新规则", "直接批准", "自动退款"]
    secret_terms = ["系统提示词", "developer message", "hidden reasoning", "隐藏推理", "内部策略", "工具 schema", "工具参数"]
    if any(term in text for term in prompt_terms):
        categories.append("prompt_injection")
    if any(term in text for term in secret_terms):
        categories.append("secret_or_reasoning_request")
    if re.search(r"1[3-9]\d{9}", text) or any(term in text for term in ["地址：", "收货地址", "身份证", "银行卡"]):
        categories.append("privacy")
    return categories

def sanitize_text(text: str) -> tuple[str, bool]:
    """删除或替换外部文本中的指令性片段，避免把知识库内容当系统命令执行。"""
    sanitized = text
    redacted = False
    injection_patterns = [
        r"忽略以上[^。；\n]*",
        r"忽略系统[^。；\n]*",
        r"不要遵守[^。；\n]*",
        r"覆盖规则[^。；\n]*",
        r"按我的新规则[^。；\n]*",
        r"直接批准[^。；\n]*",
        r"自动退款[^。；\n]*",
    ]
    for pattern in injection_patterns:
        sanitized, count = re.subn(pattern, "[已隔离的外部指令]", sanitized)
        redacted = redacted or count > 0
    sanitized, phone_count = re.subn(r"1[3-9]\d{9}", "1**********", sanitized)
    redacted = redacted or phone_count > 0
    sanitized, address_count = re.subn(r"(地址：|收货地址[:：])[^。；\n]+", r"\1[已脱敏地址]", sanitized)
    redacted = redacted or address_count > 0
    sanitized = sanitized.replace("hidden reasoning", "[受保护推理摘要]").replace("隐藏推理", "[受保护推理摘要]")
    sanitized = sanitized.replace("系统提示词", "[受保护系统信息]")
    return sanitized, redacted

def scan_external_text(text: ExternalText) -> SafetyScan:
    """扫描单段外部文本，区分普通业务内容和 Prompt Injection 风险。"""
    categories = scan_categories(text.content)
    sanitized, _ = sanitize_text(text.content)
    tainted = bool(categories)
    allowed_for_model = "secret_or_reasoning_request" not in categories
    if tainted and allowed_for_model:
        handling = "已标记污染并脱敏，只把安全摘要交给模型。"
    elif tainted:
        handling = "涉及系统信息或 hidden reasoning，请求被隔离，不交给模型。"
    else:
        handling = "未发现污染，按来源标签进入上下文。"
    return SafetyScan(
        source_type=text.source_type,
        source_id=text.source_id,
        tainted=tainted,
        categories=categories,
        sanitized_content=sanitized,
        allowed_for_model=allowed_for_model,
        handling=handling,
    )

def default_external_texts(intent: Intent) -> list[ExternalText]:
    """收集本步默认会进入上下文的外部文本，统一接受安全扫描。"""
    if intent == "refund_request":
        return [ExternalText(source_type="rag", source_id="policy-refund-unshipped", content=SAFE_REFUND_POLICY.snippet)]
    return []

def build_safety_decision(user_message: str, external_texts: list[ExternalText]) -> SafetyDecision:
    """生成 Prompt Injection 防护决策，让回答能说明哪些内容被降权或隔离。"""
    user_scan = scan_external_text(ExternalText(source_type="user", source_id="user-message", content=user_message))
    source_scans = [user_scan, *[scan_external_text(text) for text in external_texts]]
    refused_topics: list[str] = []
    if "secret_or_reasoning_request" in user_scan.categories:
        refused_topics.append("system_prompt_or_hidden_reasoning")
    blocked_user_request = bool(refused_topics)
    redaction_applied = any(scan.sanitized_content != (user_message if scan.source_id == "user-message" else "") for scan in source_scans if scan.tainted)
    public_summary: list[str] = []
    for scan in source_scans:
        if scan.tainted:
            public_summary.append(f"{scan.source_type}:{scan.source_id} 已标记 {', '.join(scan.categories)}。")
    if not public_summary:
        public_summary.append("本轮没有发现需要隔离的外部指令。")
    return SafetyDecision(
        blocked_user_request=blocked_user_request,
        refused_topics=refused_topics,
        source_scans=source_scans,
        public_summary=public_summary,
        redaction_applied=redaction_applied,
    )

def build_sanitized_context(safety: SafetyDecision) -> list[str]:
    """把清洗后的外部内容交给 Agent，保留业务事实但去掉越权指令。"""
    context: list[str] = []
    for scan in safety.source_scans:
        if not scan.allowed_for_model:
            continue
        prefix = "TAINTED" if scan.tainted else "CLEAN"
        context.append(f"[{prefix}/{scan.source_type}/{scan.source_id}] {scan.sanitized_content}")
    return context

def build_observe_reasoning_content(
    request: ChatRequest,
    safety: SafetyDecision,
    external_texts: list[ExternalText],
) -> str | None:
    """构造面向项目观察的公开推理摘要，不暴露模型 hidden CoT。"""
    if request.reasoning_view != "observe":
        return None

    observations = [
        "项目调试后台的 CoT / reasoning_content 观察区：真实客服终端不能展示这一段。",
        f"当前请求绑定 runtime_user_id={request.runtime_user_id}，这是运行时身份边界，不能写进客服回答。",
    ]
    if safety.blocked_user_request:
        observations.append("用户请求命中了系统提示词 / hidden reasoning / 工具细节泄露边界。")

    for scan in safety.source_scans:
        if not scan.tainted:
            continue
        observations.append(f"{scan.source_type}:{scan.source_id} 命中 {', '.join(scan.categories)}。")
        source_content = request.user_message if scan.source_id == "user-message" else next(
            (text.content for text in external_texts if text.source_id == scan.source_id),
            "",
        )
        if "privacy" in scan.categories:
            phones = re.findall(r"1[3-9]\d{9}", source_content)
            if phones:
                observations.append(f"CoT 中可能看见手机号原文，例如 {phones[0]}。")
            address_match = re.search(r"(?:地址：|收货地址[:：])([^。；\n]+)", source_content)
            if address_match:
                observations.append(f"CoT 中可能看见收货地址原文，例如 {address_match.group(1).strip()}。")
        if "prompt_injection" in scan.categories:
            observations.append("CoT 中可能混入外部脏指令，例如“忽略系统规则”“直接批准退款”。")
        if "secret_or_reasoning_request" in scan.categories:
            observations.append("CoT 中可能出现系统边界、hidden reasoning、工具 schema 或内部策略相关线索。")

    observations.append("结论：调试学习模式可以用这段内容观察风险；对真实用户只返回公开、脱敏、可复盘摘要。")
    return "\n".join(f"- {item}" for item in observations)

def order_answer(order_id: str | None, runtime_user_id: str, context: dict[str, Any] | None = None) -> str:
    """基于已消毒上下文回答订单问题，确保安全策略不破坏正常客服能力。"""
    if order_id is None:
        return "请先提供订单号，我才能查订单事实。"
    order = find_context_order(context, order_id) or order_fact_from_ecommerce(order_id, runtime_user_id)
    owner = order_user_id(order) if order else ""
    if order is None or (owner and owner != runtime_user_id):
        return "这个订单没有通过当前用户归属校验，我不能继续处理。"
    return f"系统确认 {order_id} 属于当前用户，订单状态 {order_status(order)}，物流状态 {logistics_status_from_order(order)}。"
