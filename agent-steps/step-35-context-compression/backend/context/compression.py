"""上下文压缩层，决定保留、摘要和丢弃哪些上下文片段。"""

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
from config.settings import MAX_CONTEXT_TOKENS, RECENT_WINDOW_SIZE
from rag.knowledge import *
from tools.runtime_context import *
from tools.planning import estimate_tokens, extract_order_ids
from integrations.ecommerce_client import order_fact_from_ecommerce
from state.session_state import SESSION_MEMORIES, SESSION_MEMORY_LOCK, SESSION_MEMORY_OWNERS

def current_memory(session_id: str, runtime_user_id: str) -> dict[str, Any]:
    """读取当前用户的会话记忆摘要，不跨用户复用，也不保存 hidden reasoning。"""
    with SESSION_MEMORY_LOCK:
        memory = SESSION_MEMORIES.get(session_id)
        if memory is None or SESSION_MEMORY_OWNERS.get(session_id) != runtime_user_id:
            # 压缩前先原子绑定属主和 Memory，避免把其他用户状态放入压缩候选。
            memory = {"last_order_id": None, "recent_intent": None}
            SESSION_MEMORIES[session_id] = memory
            SESSION_MEMORY_OWNERS[session_id] = runtime_user_id
        return memory

def runtime_context_summary(request: ChatRequest) -> str:
    """把登录态和风险信息变成高优先级上下文，提醒模型这些事实比历史对话更可信。"""
    member_level = request.runtime_member_level or "unknown"
    risk_level = request.runtime_risk_level or "unknown"
    page = request.runtime_context or {}
    page_order = page.get("current_order_id") or page.get("relatedOrderNo")
    page_text = f"，页面订单 {page_order}" if page_order else ""
    return f"可信运行时上下文：用户 {request.runtime_user_id}，会员 {member_level}，风险 {risk_level}{page_text}。"

def build_order_context(order_id: str | None, runtime_user_id: str, context: dict[str, Any] | None = None) -> tuple[ContextCandidate | None, dict[str, Any] | None]:
    """构造订单相关上下文候选，把工具事实和用户可见订单快照放在受保护位置。"""
    if not order_id:
        return None, None
    order = find_context_order(context, order_id) or order_fact_from_ecommerce(order_id, runtime_user_id)
    owner = order_user_id(order) if order else ""
    if order is None or (owner and owner != runtime_user_id):
        return ContextCandidate(
            item_id="tool-order-observation",
            source_type="tool_observation",
            content=f"订单 {order_id} 没有通过当前用户归属校验。",
            token_estimate=estimate_tokens(order_id),
            relevance_score=95,
            protected=True,
            keep_reason="订单权限校验结果必须保留。",
        ), None
    names = item_names(order)
    product_text = names[0] if names else "商品明细以订单系统为准"
    content = f"工具观察：{order_id} 状态 {order_status(order)}，物流 {logistics_status_from_order(order)}，商品 {product_text}。"
    return ContextCandidate(
        item_id="tool-order-observation",
        source_type="tool_observation",
        content=content,
        token_estimate=estimate_tokens(content),
        relevance_score=95,
        protected=True,
        keep_reason="当前问题直接依赖订单事实。",
    ), order

def build_workflow_context(intent: Intent, order_id: str | None) -> ContextCandidate | None:
    """把售后流程状态放入上下文候选，防止长历史挤掉待审批等关键状态。"""
    if intent != "refund_request":
        return None
    content = f"Workflow State：订单 {order_id or '未确认'} 的退款流程必须等待人工审批，不能直接动钱。"
    return ContextCandidate(
        item_id="workflow-state",
        source_type="workflow_state",
        content=content,
        token_estimate=estimate_tokens(content),
        relevance_score=100,
        protected=True,
        keep_reason="高风险 workflow 状态不能被压缩掉。",
    )

def build_rag_context(intent: Intent) -> ContextCandidate | None:
    """把政策引用转成上下文候选，并保留来源信息供回答引用。"""
    if intent != "refund_request":
        return None
    policy = POLICIES["POLICY-REFUND-UNSHIPPED"]
    return ContextCandidate(
        item_id="rag-refund-policy",
        source_type="rag_snippet",
        content=f"RAG 片段：{policy.snippet}",
        token_estimate=estimate_tokens(policy.snippet),
        relevance_score=90,
        protected=False,
        keep_reason="当前退款问题需要政策依据。",
    )

class ContextCompressor:
    """Step 35 的上下文窗口选择和压缩器。

    这里不用模型做摘要，而是用确定性规则演示工程边界：保护可信系统上下文、
    工具事实和 workflow state，再按相关性与最近窗口选择历史消息。
    """

    def compress(self, candidates: list[ContextCandidate], *, current_message: str) -> CompressionReport:
        """按保护项、最近窗口和相关性压缩上下文，展示生产系统不会简单截断历史。"""
        before = sum(item.token_estimate for item in candidates)
        protected = [item for item in candidates if item.protected]
        optional = [item for item in candidates if not item.protected]
        kept: list[ContextCandidate] = list(protected)
        budget = MAX_CONTEXT_TOKENS - sum(item.token_estimate for item in kept)
        for item in sorted(optional, key=context_sort_key, reverse=True):
            if item.token_estimate <= budget or item.relevance_score >= 85:
                kept.append(item)
                budget -= item.token_estimate

        kept_ids = {item.item_id for item in kept}
        dropped = [item for item in candidates if item.item_id not in kept_ids]
        compressed_summary = summarize_dropped_items(dropped)
        if compressed_summary:
            summary_item = ContextCandidate(
                item_id="compressed-history-summary",
                source_type="history_message",
                content=compressed_summary,
                token_estimate=estimate_tokens(compressed_summary),
                relevance_score=60,
                protected=False,
                keep_reason="旧历史压缩为公开摘要，避免长上下文淹没当前事实。",
            )
            kept.append(summary_item)

        after = sum(item.token_estimate for item in kept)
        return CompressionReport(
            max_context_tokens=MAX_CONTEXT_TOKENS,
            recent_window_size=RECENT_WINDOW_SIZE,
            input_items=candidates,
            kept_items=kept,
            compressed_summary=compressed_summary,
            dropped_items=dropped,
            token_estimate_before=before,
            token_estimate_after=after,
            lost_in_middle_guardrails=[
                "Runtime Context、工具 Observation 和 Workflow State 标记为 protected。",
                "最近消息用 Sliding Window 保留。",
                "中间历史如果命中当前订单号或当前意图，按相关性保留。",
                "旧闲聊只进入压缩摘要，不直接占模型窗口。",
            ],
        )

def context_sort_key(candidate: ContextCandidate) -> tuple[int, int]:
    """给候选上下文排序，让压缩报告能解释为什么某些信息被保留。"""
    if candidate.item_id.startswith("history-"):
        return candidate.relevance_score, int(candidate.item_id.removeprefix("history-"))
    return candidate.relevance_score, 10_000

def summarize_dropped_items(dropped: list[ContextCandidate]) -> str:
    """把被丢弃的旧上下文压成公开摘要，降低遗忘感但不继续塞满窗口。"""
    if not dropped:
        return ""
    order_ids: list[str] = []
    for item in dropped:
        for order_id in extract_order_ids(item.content):
            if order_id not in order_ids:
                order_ids.append(order_id)
    order_text = f"；旧历史提到过订单 {', '.join(order_ids)}" if order_ids else ""
    return f"压缩摘要：已折叠 {len(dropped)} 条低相关历史消息{order_text}。"

def build_history_candidates(history: list[HistoryMessage], current_message: str) -> list[ContextCandidate]:
    """把历史消息转成可评分候选，便于 Sliding Window 和相关性规则共同工作。"""
    current_order_ids = set(extract_order_ids(current_message))
    candidates: list[ContextCandidate] = []
    total = len(history)
    for index, message in enumerate(history):
        is_recent = index >= max(0, total - RECENT_WINDOW_SIZE)
        shares_order = bool(current_order_ids.intersection(extract_order_ids(message.content)))
        relevance = 82 if is_recent else 35
        keep_reason = "最近窗口消息。" if is_recent else None
        if shares_order:
            relevance = 92
            keep_reason = "虽然在中间历史，但命中当前订单号，防止 Lost in the Middle。"
        candidates.append(
            ContextCandidate(
                item_id=f"history-{index}",
                source_type="history_message",
                content=f"{message.role}: {message.content}",
                token_estimate=estimate_tokens(message.content),
                relevance_score=relevance,
                protected=False,
                keep_reason=keep_reason,
            )
        )
    return candidates
