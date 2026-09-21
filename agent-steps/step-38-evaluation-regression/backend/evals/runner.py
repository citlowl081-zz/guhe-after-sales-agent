"""评测执行层，用固定用例检查回答、工具、引用、Trace 和状态。"""

from __future__ import annotations

from guhe_runtime.guhe_logging import log_guhe_event, observe_evaluation

import json
import os
import re
from pathlib import Path
from typing import Any, Literal
from uuid import uuid4

import httpx
import yaml
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from api.schemas import *
from config.settings import CASES_PATH
from observability.trace import trace_store

class EvalRunner:
    """项目型离线回归评测。

    Eval 不读 hidden CoT，也不让测试伪造模型或工具结果；它只调用本步真实 Agent，
    再检查公开响应字段和 trace 事件是否满足固定 case 的结构化期望。
    """

    def __init__(self, agent: Step38Agent, cases_path: Path) -> None:
        """初始化本步服务对象，把可替换依赖固定在实例上，便于测试和项目演进。"""
        self.agent = agent
        self.cases_path = cases_path

    def load_cases(self) -> list[dict[str, Any]]:
        """读取固定评测用例，并合并本步从反馈回填的临时回归 case。"""
        payload = yaml.safe_load(self.cases_path.read_text(encoding="utf-8"))
        return payload["cases"]

    @observe_evaluation
    def run(self, case_id: str | None = None) -> EvalRunResponse:
        """运行离线回归评测，用公开响应、工具、引用、Trace 和状态检查 Agent 是否退化。"""
        selected = [case for case in self.load_cases() if case_id in (None, case["case_id"])]
        results: list[EvalCaseResult] = []
        run_id = uuid4().hex[:8]
        for case in selected:
            session_id = f"eval-{case['case_id']}-{run_id}"
            response = self.agent.chat(
                ChatRequest(
                    session_id=session_id,
                    runtime_user_id=case.get("runtime_user_id", "U1001"),
                    runtime_nickname=case.get("runtime_nickname"),
                    runtime_member_level=case.get("runtime_member_level"),
                    runtime_risk_level=case.get("runtime_risk_level"),
                    user_message=case["user_message"],
                    runtime_context=case.get("runtime_context"),
                )
            )
            trace_events = trace_store.list(session_id)
            actual_tools = [call.tool_name for call in response.tool_calls]
            actual_citations = self._citation_signals(response.citations)
            actual_trace_events = [event.event_type for event in trace_events]
            actual_text = " ".join(
                [response.answer]
                + response.reasoning_summary
                + actual_tools
                + actual_citations
                + actual_trace_events
                + self._flatten_values(response.session_state)
            )
            missing_signals = [signal for signal in case.get("expected_signals", []) if signal not in actual_text]
            missing_tools = [tool for tool in case.get("expected_tools", []) if tool not in actual_tools]
            unexpected_tools = [tool for tool in case.get("forbidden_tools", []) if tool in actual_tools]
            missing_citations = [
                item for item in case.get("expected_citations", []) if not self._contains_any(actual_citations, item)
            ]
            forbidden_citation_hits = [
                item for item in case.get("forbidden_citations", []) if self._contains_any(actual_citations, item)
            ]
            missing_trace_events = [
                event for event in case.get("expected_trace_events", []) if event not in actual_trace_events
            ]
            missing_session_state = [
                requirement
                for requirement in case.get("expected_session_state", [])
                if not self._matches_session_state(response.session_state, requirement)
            ]
            forbidden_text_hits = [text for text in case.get("forbidden_text", []) if text in actual_text]
            failure_categories = self._failure_categories(
                missing_signals=missing_signals,
                missing_tools=missing_tools,
                unexpected_tools=unexpected_tools,
                missing_citations=missing_citations,
                forbidden_citation_hits=forbidden_citation_hits,
                missing_trace_events=missing_trace_events,
                missing_session_state=missing_session_state,
                forbidden_text_hits=forbidden_text_hits,
            )
            results.append(
                EvalCaseResult(
                    case_id=case["case_id"],
                    passed=not failure_categories,
                    user_message=case["user_message"],
                    expected_signals=case.get("expected_signals", []),
                    actual_answer=response.answer,
                    actual_tools=actual_tools,
                    missing_signals=missing_signals,
                    actual_citations=actual_citations,
                    actual_trace_events=actual_trace_events,
                    missing_tools=missing_tools,
                    unexpected_tools=unexpected_tools,
                    missing_citations=missing_citations,
                    forbidden_citation_hits=forbidden_citation_hits,
                    missing_trace_events=missing_trace_events,
                    missing_session_state=missing_session_state,
                    forbidden_text_hits=forbidden_text_hits,
                    failure_categories=failure_categories,
                )
            )
            log_guhe_event("EVAL_CASE", "Evaluation Case 执行完成", case_id=case["case_id"], passed=results[-1].passed, failure_categories=results[-1].failure_categories)
        passed = len([result for result in results if result.passed])
        return EvalRunResponse(
            total=len(results),
            passed=passed,
            failed=len(results) - passed,
            summary=self._summary(results),
            results=results,
        )

    @staticmethod
    def _citation_signals(citations: list[Citation]) -> list[str]:
        """把引用对象展平成可匹配信号，便于规则化评测检查来源。"""
        signals: list[str] = []
        for citation in citations:
            signals.extend([citation.source, citation.title, citation.retrieval_stage or ""])
            if citation.metadata:
                signals.extend(str(value) for value in citation.metadata.values())
        return [signal for signal in signals if signal]

    @classmethod
    def _flatten_values(cls, value: Any) -> list[str]:
        """展开嵌套 session_state，用于匹配 workflow 和成本治理等公开状态。"""
        if isinstance(value, dict):
            values: list[str] = []
            for item in value.values():
                values.extend(cls._flatten_values(item))
            return values
        if isinstance(value, list):
            values: list[str] = []
            for item in value:
                values.extend(cls._flatten_values(item))
            return values
        if value is None:
            return []
        return [str(value)]

    @staticmethod
    def _contains_any(values: list[str], expected: str) -> bool:
        """检查实际信号是否包含期望片段，避免把评测写成脆弱的全文相等。"""
        return any(expected in value for value in values)

    @classmethod
    def _matches_session_state(cls, session_state: dict[str, Any], requirement: str) -> bool:
        """按点路径检查 session_state，确保关键业务状态没有丢失。"""
        if "=" not in requirement:
            return cls._lookup(session_state, requirement) is not None
        path, expected = requirement.split("=", 1)
        actual = cls._lookup(session_state, path.strip())
        return cls._normalize_scalar(actual) == expected.strip()

    @staticmethod
    def _lookup(payload: dict[str, Any], path: str) -> Any:
        """在嵌套字典中读取点路径字段，服务于 Eval 的状态断言。"""
        current: Any = payload
        normalized_path = path.removeprefix("session_state.")
        for part in normalized_path.split("."):
            if not isinstance(current, dict) or part not in current:
                return None
            current = current[part]
        return current

    @staticmethod
    def _normalize_scalar(value: Any) -> str:
        """把布尔和空值归一成字符串，让 YAML 期望更稳定。"""
        if isinstance(value, bool):
            return "true" if value else "false"
        if value is None:
            return "null"
        return str(value)

    @staticmethod
    def _failure_categories(
        *,
        missing_signals: list[str],
        missing_tools: list[str],
        unexpected_tools: list[str],
        missing_citations: list[str],
        forbidden_citation_hits: list[str],
        missing_trace_events: list[str],
        missing_session_state: list[str],
        forbidden_text_hits: list[str],
    ) -> list[str]:
        """把缺失项归并成问题类别，为后续反馈归因提供入口。"""
        categories: list[str] = []
        if missing_signals:
            categories.append("answer_signal_missing")
        if missing_tools or unexpected_tools:
            categories.append("tool_path_mismatch")
        if missing_citations or forbidden_citation_hits:
            categories.append("citation_missing")
        if missing_trace_events:
            categories.append("trace_event_missing")
        if missing_session_state:
            categories.append("session_state_mismatch")
        if forbidden_text_hits:
            categories.append("forbidden_text_present")
        return categories

    @staticmethod
    def _summary(results: list[EvalCaseResult]) -> dict[str, Any]:
        """汇总一次评测运行结果，给项目观察台展示失败 case 和失败类别。"""
        failed_cases = [result.case_id for result in results if not result.passed]
        failure_categories: dict[str, int] = {}
        for result in results:
            for category in result.failure_categories:
                failure_categories[category] = failure_categories.get(category, 0) + 1
        return {
            "schema_version": "eval_report_v1",
            "failed_cases": failed_cases,
            "failure_categories": failure_categories,
            "checked_dimensions": ["answer", "tool_calls", "citations", "trace", "session_state", "workflow"],
            "boundary": "规则化离线评测用于项目回归，不等同于线上监控、人工抽检或 LLM-as-judge 平台。",
        }
