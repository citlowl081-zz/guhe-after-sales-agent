"""成本治理层，记录缓存、prompt 片段、工具调用和 Observation 压缩。"""

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
from tools.planning import estimate_tokens

COMMON_HIT_CACHE: dict[str, dict[str, Any]] = {}

def selected_prompt_fragments(intent: Intent, *, has_citations: bool, has_workflow: bool) -> list[str]:
    """根据请求路径选择 Prompt 片段，展示成本治理会控制上下文组成。"""
    fragments = ["role_guhe_customer_service", "safety_public_answer_boundary"]
    if intent == "order_query":
        fragments.append("tool_observation_policy")
    if has_citations:
        fragments.append("rag_citation_answer_policy")
    if has_workflow:
        fragments.append("high_risk_after_sale_hitl_boundary")
    return fragments

def observation_compression(tool_calls: list[ToolCallTrace]) -> dict[str, Any]:
    """压缩工具 Observation 摘要，保留调试信号但降低上下文成本。"""
    original_tokens = sum(estimate_tokens(call.output_summary) for call in tool_calls)
    # Observation 进入模型前只保留状态、下一步和风险摘要，不把工具原始结果整段塞回去。
    compressed_tokens = sum(min(12, estimate_tokens(call.output_summary)) for call in tool_calls)
    return {
        "schema_version": "observation_compression_v1",
        "original_tokens": original_tokens,
        "compressed_tokens": compressed_tokens,
        "saved_tokens": max(0, original_tokens - compressed_tokens),
        "strategy": "keep_status_next_action_and_risk_summary",
    }

def build_cost_summary(
    *,
    request: ChatRequest,
    intent: Intent,
    tool_calls: list[ToolCallTrace],
    citations: list[Citation],
    workflow: dict[str, Any] | None,
    answer: str,
    cache_hit: bool,
    answer_model_used: bool = False,
) -> dict[str, Any]:
    """生成请求级成本摘要，把模型、RAG、工具、缓存和预算信号集中呈现。"""
    has_workflow = bool(workflow)
    path_type = "cached_faq_light_path" if cache_hit else "langgraph_after_sale_workflow" if has_workflow else "light_react_agent"
    fragments = selected_prompt_fragments(intent, has_citations=bool(citations), has_workflow=has_workflow)
    context_tokens = estimate_tokens(request.user_message) + sum(estimate_tokens(call.output_summary) for call in tool_calls)
    prompt_tokens = 18 * len(fragments) + sum(estimate_tokens(citation.snippet) for citation in citations)
    answer_tokens = estimate_tokens(answer)
    if cache_hit:
        prompt_tokens = max(8, prompt_tokens // 3)
        answer_tokens = max(4, answer_tokens // 2)
    total_tokens = context_tokens + prompt_tokens + answer_tokens
    compressed = observation_compression(tool_calls)
    warnings: list[str] = []
    if total_tokens > 160:
        warnings.append("total_estimated_tokens_over_observe_budget")
    if len(tool_calls) > 3:
        warnings.append("tool_call_count_high")
    return {
        "schema_version": "cost_summary_v1",
        "cost_profile": "observe",
        "path_type": path_type,
        "model_calls": {
            "route_planner": 1,
            "final_answer": 1 if answer_model_used else 0,
            "extra_reasoning": 0,
        },
        "tool_call_count": len(tool_calls),
        "business_tool_call_count": sum(1 for call in tool_calls if call.tool_name != "retrieve_knowledge"),
        "rag": {
            "needs_rag": bool(citations),
            "hit_count": len(citations),
            "pre_retrieval_count": 0 if cache_hit else (1 if citations else 0),
            "tool_retrieval_count": 0,
            "retrieval_paths": [citation.retrieval_stage for citation in citations if citation.retrieval_stage],
            "cache_hit": cache_hit,
        },
        "tokens": {
            "context_estimated": context_tokens,
            "prompt_estimated": prompt_tokens,
            "answer_estimated": answer_tokens,
            "total_estimated": total_tokens,
            "context_budget": 160,
        },
        "prompt_fragments": {
            "selected": fragments,
            "fragment_count": len(fragments),
            "fragmentized": True,
        },
        "cache": {
            "common_hit_cache": cache_hit,
            "cache_key": "faq:invoice_issue" if "发票" in request.user_message else None,
            "does_not_cache_high_risk_workflow": True,
        },
        "observation_compression": compressed,
        "workflow": {
            "used_langgraph": has_workflow,
            "workflow_id": workflow.get("workflow_id") if workflow else None,
            "workflow_type": workflow.get("workflow_type") if workflow else None,
            "hitl_required": bool(workflow and workflow.get("pending_action") == "require_approval"),
            "status": workflow.get("status") if workflow else None,
        },
        "degradation": {
            "degraded": False,
            "reason": None,
            "cost_threshold_exceeded": bool(warnings),
            "warnings": warnings,
        },
        "safety_boundary": {
            "cost_control_does_not_skip_business_facts": True,
            "cost_control_does_not_skip_hitl": True,
            "not_finops_or_billing_system": True,
        },
    }
