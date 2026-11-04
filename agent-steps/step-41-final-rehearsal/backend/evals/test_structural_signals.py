"""Step 41 评测回归：事实落在工具摘要和引用片段上，不落在模型改写后的话术上。"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
AGENT_STEPS = BACKEND.parents[1]
sys.path.insert(0, str(BACKEND))
sys.path.insert(0, str(AGENT_STEPS))

import yaml

from api.schemas import ChatResponse, Citation, ToolCallTrace
from evals.runner import EvalRunner


CASES_PATH = BACKEND / "cases.yml"


class _ScriptedAgent:
    def __init__(self, response: ChatResponse) -> None:
        self.response = response

    def chat(self, request: object) -> ChatResponse:
        return self.response


def _runner(case: dict, response: ChatResponse) -> EvalRunner:
    payload = {"cases": [case]}
    with tempfile.NamedTemporaryFile("w", suffix=".yml", encoding="utf-8", delete=False) as handle:
        yaml.safe_dump(payload, handle, allow_unicode=True)
        path = Path(handle.name)
    return EvalRunner(_ScriptedAgent(response), path)


def _response(
    *,
    answer: str,
    tool_summary: str = "",
    snippet: str = "",
    policy_id: str = "",
) -> ChatResponse:
    tool_calls = []
    if tool_summary:
        tool_calls.append(
            ToolCallTrace(
                tool_name="get_order_logistics",
                arguments={"order_id": "SO-1"},
                output_summary=tool_summary,
                status="success",
            )
        )
    citations = []
    if snippet or policy_id:
        citations.append(
            Citation(
                source="knowledge/promotion_policy.md",
                title="618 大促活动规则",
                snippet=snippet,
                score=0.9,
                metadata={"policy_id": policy_id} if policy_id else None,
            )
        )
    return ChatResponse(
        session_id="scripted",
        answer=answer,
        citations=citations,
        tool_calls=tool_calls,
        reasoning_summary=[],
        session_state={"model": {"final_answer": {"used_model": True}}},
    )


class StructuralSignalTests(unittest.TestCase):
    def test_answer_phrase_cannot_replace_a_missing_tool_fact(self) -> None:
        runner = _runner(
            {
                "case_id": "logistics-fact",
                "user_message": "查物流",
                "expected_tool_outputs": ["运输中"],
            },
            _response(answer="物流状态是运输中。", tool_summary="没有查到物流。"),
        )
        result = runner.run().results[0]
        self.assertFalse(result.passed)
        self.assertIn("运输中", result.missing_tool_outputs)
        self.assertIn("tool_path_mismatch", result.failure_categories)

    def test_paraphrased_answer_passes_when_tool_fact_is_present(self) -> None:
        runner = _runner(
            {
                "case_id": "logistics-fact",
                "user_message": "查物流",
                "expected_tool_outputs": ["运输中"],
            },
            _response(answer="包裹已经在路上。", tool_summary="物流状态为运输中。"),
        )
        result = runner.run().results[0]
        self.assertTrue(result.passed, result.missing_tool_outputs)

    def test_answer_phrase_cannot_replace_a_missing_citation_snippet(self) -> None:
        runner = _runner(
            {
                "case_id": "promotion-fact",
                "user_message": "满减怎么叠？",
                "expected_citations": ["promotion_618_stack_rule"],
                "expected_citation_snippets": ["满 300 减 40"],
            },
            _response(
                answer="满 300 减 40 可以叠加。",
                snippet="活动规则已更新。",
                policy_id="promotion_618_stack_rule",
            ),
        )
        result = runner.run().results[0]
        self.assertFalse(result.passed)
        self.assertIn("满 300 减 40", result.missing_citation_snippets)
        self.assertIn("citation_missing", result.failure_categories)

    def test_paraphrased_answer_passes_when_citation_snippet_holds_the_fact(self) -> None:
        runner = _runner(
            {
                "case_id": "promotion-fact",
                "user_message": "满减怎么叠？",
                "expected_citation_snippets": ["满 300 减 40"],
            },
            _response(answer="活动满减可以和会员券一起用。", snippet="满 300 减 40 活动可以与平台会员券叠加。"),
        )
        result = runner.run().results[0]
        self.assertTrue(result.passed, result.missing_citation_snippets)

    def test_forbidden_answer_still_fails_when_workflow_state_looks_eligible(self) -> None:
        runner = _runner(
            {
                "case_id": "return-boundary",
                "user_message": "我要退货",
                "expected_session_state": ["workflow.eligibility_status=eligible_for_application"],
                "forbidden_text": ["已退货成功"],
            },
            ChatResponse(
                session_id="scripted",
                answer="当前已退货成功，请查收。",
                citations=[],
                tool_calls=[],
                reasoning_summary=[],
                session_state={"workflow": {"eligibility_status": "eligible_for_application"}},
            ),
        )
        result = runner.run().results[0]
        self.assertFalse(result.passed)
        self.assertIn("已退货成功", result.forbidden_text_hits)

    def test_fixed_cases_keep_flaky_phrases_off_the_model_answer(self) -> None:
        payload = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
        cases = {case["case_id"]: case for case in payload["cases"]}

        logistics = cases["order-logistics-tool-path"]
        self.assertNotIn("运输中", logistics.get("expected_signals", []))
        self.assertIn("运输中", logistics["expected_tool_outputs"])

        promotion = cases["promotion-member-rag-path"]
        self.assertNotIn("满 300 减 40", promotion.get("expected_signals", []))
        self.assertNotIn("金卡会员", promotion.get("expected_signals", []))
        self.assertIn("满 300 减 40", promotion["expected_citation_snippets"])
        self.assertIn("金卡会员", promotion["expected_citation_snippets"])

        product = cases["product-tool-rag-joint-answer"]
        claim_labels = [claim.get("label") for claim in product.get("expected_answer_claims", [])]
        self.assertNotIn("满 300 减 40", claim_labels)
        self.assertIn("599", product["expected_tool_outputs"])
        self.assertIn("满 300 减 40", product["expected_citation_snippets"])
        self.assertTrue(product.get("forbidden_answer_claims"))

        received = cases["received-return-workflow"]
        received_labels = [claim.get("label") for claim in received.get("expected_answer_claims", [])]
        self.assertNotIn("当前还不是退货成功", received_labels)
        self.assertIn("workflow.eligibility_status=eligible_for_application", received["expected_session_state"])
        self.assertIn("已退货成功", received["forbidden_text"])

        faq = cases["faq-dynamic-knowledge-selection"]
        self.assertNotIn("修改发票抬头", faq.get("expected_signals", []))
        self.assertIn("修改发票抬头", faq["expected_citation_snippets"])

        refund_status = cases["refund-status-read-only"]
        self.assertNotIn("审核中", refund_status.get("expected_signals", []))
        self.assertIn("审核中", refund_status["expected_tool_outputs"])

    def test_case_ids_are_unique_and_cover_the_expanded_branches(self) -> None:
        payload = yaml.safe_load(CASES_PATH.read_text(encoding="utf-8"))
        case_ids = [case["case_id"] for case in payload["cases"]]
        self.assertEqual(len(case_ids), len(set(case_ids)))
        self.assertGreaterEqual(len(case_ids), 182)
        retrieval = yaml.safe_load((BACKEND / "evals" / "retrieval_questions.yml").read_text(encoding="utf-8"))
        retrieval_ids = [item["id"] for item in retrieval["retrieval"]]
        abstain_ids = [item["id"] for item in retrieval["abstain"]]
        workflow_ids = [item["id"] for item in retrieval["workflow"]]
        all_ids = retrieval_ids + abstain_ids + workflow_ids
        self.assertEqual(len(all_ids), len(set(all_ids)))
        self.assertGreaterEqual(len(retrieval_ids), 157)
        self.assertGreaterEqual(len(abstain_ids), 244)
        self.assertGreaterEqual(len(workflow_ids), 112)
        for item in retrieval["retrieval"]:
            self.assertTrue(item["expected_policy_ids"])
        required = {
            "return-window-expired",
            "return-reason-missing",
            "refund-owner-mismatch",
            "refund-missing-order-clarification",
            "promotion-rule-only",
            "member-coupon-only",
            "unpublished-promotion-low-confidence",
            "refund-resume-rejected",
            "security-tool-schema",
            "refund-clarification-then-order-id",
            "history-older-turns-dropped",
            "runtime-identity-from-context",
        }
        self.assertTrue(required <= set(case_ids))

    def test_case_exception_is_reported_instead_of_aborting_the_run(self) -> None:
        class _BrokenAgent:
            def chat(self, request: object) -> ChatResponse:
                raise RuntimeError("embedding unavailable")

        payload = {"cases": [{"case_id": "broken", "user_message": "查物流"}]}
        with tempfile.NamedTemporaryFile("w", suffix=".yml", encoding="utf-8", delete=False) as handle:
            yaml.safe_dump(payload, handle, allow_unicode=True)
            path = Path(handle.name)
        result = EvalRunner(_BrokenAgent(), path).run().results[0]
        self.assertFalse(result.passed)
        self.assertIn("case_execution_error", result.failure_categories)
        self.assertIn("embedding unavailable", result.actual_answer)


if __name__ == "__main__":
    unittest.main()
