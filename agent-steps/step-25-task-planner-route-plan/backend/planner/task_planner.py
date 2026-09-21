"""Step 25：RoutePlan 规划器。规则优先，低置信时再请求模型补充结构化计划。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
from models.task_planner_client import TaskPlannerModelClient
from tools.catalog import ToolCatalog


class TaskPlanner:
    """TaskPlanner 承载本步逐步长出的 Agent 模块能力。"""
    ORDER_ID_PATTERN = re.compile(r"\b(?:SO[A-Za-z0-9_-]{6,}|[A-Za-z0-9][A-Za-z0-9_-]{5,})\b")
    NEGATED_AFTER_SALE_PATTERN = re.compile(
        r"(?:不是|并非|不想|不打算|不需要|无需|不用|不要)"
        r"(?:想要|想|要|申请|办理)?(?:退款|退货|取消订单|补偿|退钱)"
    )
    ALLOWED_MODEL_INTENTS = {
        "general",
        "order_logistics",
        "after_sale_policy",
        "product_consult",
        "product_availability_and_promotion",
        "promotion_faq",
        "invoice_policy",
    }
    ALLOWED_PLAN_INTENTS = ALLOWED_MODEL_INTENTS | {
        "after_sale_eligibility",
        "check_product_realtime_fact",
        "check_member_promotion",
        "promotion_policy",
    }
    ALLOWED_KNOWLEDGE_DOMAINS = {"after_sale_policy", "product_guide", "promotion_policy", "invoice_policy"}
    ALLOWED_ENTITY_REFS = {"order_id", "product_id"}
    ALLOWED_REQUIRED_CONTEXT = {"runtime_context", "session_memory"}
    ALLOWED_FALLBACK_POLICIES = {
        "default",
        "tool_first",
        "knowledge_only",
        "tool_first_then_policy_caveat",
        "workflow_first",
        "clarify_or_model_plan",
    }
    MIN_MODEL_CONFIDENCE = 0.65

    def __init__(self, tool_catalog: ToolCatalog, model_client: TaskPlannerModelClient | None = None) -> None:
        """初始化项目对象需要的协作模块，让 main.py 保持薄入口。"""
        self.tool_catalog = tool_catalog
        self.model_client = model_client

    async def plan(
        self,
        user_message: str,
        session_memory: dict[str, Any] | None = None,
        runtime_context: dict[str, Any] | None = None,
    ) -> tuple[RoutePlan, PlannerTrace]:
        """生成本轮 RoutePlan，并把规则、模型和工具候选约束收口到统一结果。"""
        rule_plan = self._apply_safety_constraints(
            user_message,
            self.plan_by_rules(user_message, session_memory or {}, runtime_context or {}),
        )
        candidates = self.tool_catalog.candidate_summaries(rule_plan)

        if rule_plan.confidence >= 0.85:
            constrained = self._constrain_required_tools(rule_plan, candidates)
            return constrained, self._build_trace(rule_plan, constrained, candidates)

        model_decision = await self._plan_task_with_candidates(user_message, candidates)
        if model_decision:
            model_plan = self._route_plan_from_model_decision(model_decision, rule_plan, candidates, user_message)
            if model_plan.confidence >= self.MIN_MODEL_CONFIDENCE:
                safe_model_plan = self._apply_safety_constraints(user_message, model_plan)
                constrained = self._constrain_required_tools(safe_model_plan, candidates)
                return constrained, self._build_trace(rule_plan, constrained, candidates)

        fallback_plan = rule_plan.model_copy(update={"source": "rules_fallback"})
        constrained = self._constrain_required_tools(fallback_plan, candidates)
        return constrained, self._build_trace(rule_plan, constrained, candidates)

    def _build_trace(
        self,
        rule_plan: RoutePlan,
        constrained: RoutePlan,
        candidates: list[ToolCandidate],
    ) -> PlannerTrace:
        """构造公开 Planner 轨迹，让路由原因可观察但不暴露隐藏推理。"""
        return PlannerTrace(
            source=constrained.source,
            rule_confidence=rule_plan.confidence,
            candidate_tools=candidates,
            constrained_required_tools=constrained.required_tools,
            public_reason=self._public_reason(constrained),
        )

    async def _plan_task_with_candidates(
        self,
        user_message: str,
        candidates: list[ToolCandidate],
    ) -> dict[str, Any] | None:
        """把候选工具摘要交给分类模型，只让模型输出结构化路由字段。"""
        if self.model_client is None:
            return None
        tool_candidates = [candidate.model_dump() for candidate in candidates]
        return await self.model_client.plan_task(user_message, tool_candidates)

    def _route_plan_from_model_decision(
        self,
        model_decision: dict[str, Any],
        rule_plan: RoutePlan,
        candidates: list[ToolCandidate],
        user_message: str,
    ) -> RoutePlan:
        """把模型 JSON 决策装配成可校验的 RoutePlan。"""
        proposed_intent = str(model_decision.get("intent") or rule_plan.intent).strip() or rule_plan.intent
        intent = proposed_intent if proposed_intent in self.ALLOWED_MODEL_INTENTS else rule_plan.intent
        required_tools = self.tool_catalog.allowed_required_tools(
            self._list_value(model_decision.get("required_tools")) or rule_plan.required_tools,
            candidates,
        )
        has_realtime_fact = self._bool_value(model_decision.get("has_realtime_fact"), rule_plan.has_realtime_fact)
        is_product_intent = "product" in intent or rule_plan.intent.startswith("product")
        risk_level = str(model_decision.get("risk_level") or rule_plan.risk_level).strip().lower()
        if risk_level not in ("low", "medium", "high"):
            risk_level = rule_plan.risk_level

        proposed_domains = self._list_value(model_decision.get("knowledge_domains"))
        knowledge_domains = [domain for domain in proposed_domains if domain in self.ALLOWED_KNOWLEDGE_DOMAINS]
        proposed_intents = [
            item for item in self._list_value(model_decision.get("intents")) if item in self.ALLOWED_PLAN_INTENTS
        ]
        entity_refs = [
            item for item in self._list_value(model_decision.get("entity_refs")) if item in self.ALLOWED_ENTITY_REFS
        ]
        required_context = [
            item
            for item in self._list_value(model_decision.get("required_context"))
            if item in self.ALLOWED_REQUIRED_CONTEXT
        ]
        proposed_fallback = str(model_decision.get("fallback_policy") or rule_plan.fallback_policy)
        fallback_policy = (
            proposed_fallback if proposed_fallback in self.ALLOWED_FALLBACK_POLICIES else rule_plan.fallback_policy
        )

        return RoutePlan(
            intent=intent,
            needs_rag=self._bool_value(model_decision.get("needs_rag"), rule_plan.needs_rag),
            needs_business_tools=self._bool_value(
                model_decision.get("needs_business_tools"),
                rule_plan.needs_business_tools,
            )
            or is_product_intent
            or has_realtime_fact
            or bool(required_tools),
            rag_query=str(model_decision.get("rag_query") or rule_plan.rag_query or user_message),
            confidence=self._confidence_value(model_decision.get("confidence"), rule_plan.confidence),
            source="classifier",
            intents=proposed_intents or rule_plan.intents or [intent],
            entity_refs=entity_refs or rule_plan.entity_refs,
            required_context=required_context or rule_plan.required_context,
            required_tools=required_tools,
            knowledge_domains=knowledge_domains or rule_plan.knowledge_domains,
            has_realtime_fact=has_realtime_fact,
            risk_level=risk_level,
            requires_workflow=self._bool_value(model_decision.get("requires_workflow"), rule_plan.requires_workflow),
            fallback_policy=fallback_policy,
        )

    @staticmethod
    def _list_value(value: Any) -> list[str]:
        """把模型输出里的列表字段规范成字符串列表。"""
        if isinstance(value, list):
            return [str(item).strip() for item in value if item not in (None, "") and str(item).strip()]
        if isinstance(value, str) and value.strip():
            return [value.strip()]
        return []

    @staticmethod
    def _bool_value(value: Any, default: bool) -> bool:
        """把模型输出里的布尔字段规范成安全默认值。"""
        if value is None:
            return default
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            lowered = value.strip().lower()
            if lowered in {"true", "1", "yes", "y", "是"}:
                return True
            if lowered in {"false", "0", "no", "n", "否"}:
                return False
        return default

    @staticmethod
    def _confidence_value(value: Any, default: float) -> float:
        """把模型置信度压到 0 到 1 之间，方便后续阈值判断。"""
        try:
            confidence = float(value)
        except (TypeError, ValueError):
            confidence = default
        return max(0.0, min(1.0, confidence))

    def plan_by_rules(self, user_message: str, session_memory: dict[str, Any], runtime_context: dict[str, Any]) -> RoutePlan:
        """用确定性规则先生成高置信 RoutePlan，降低模型调用成本和漂移。"""
        text = user_message.strip()
        has_order_id = bool(self.ORDER_ID_PATTERN.search(text))
        signals = self._route_signals(text)

        # 带订单号的退款、取消和补偿必须先于物流等轻路径，避免复合问题漏掉高风险动作。
        if signals["after_sale"] and has_order_id:
            return self._decision(
                intent="after_sale_policy",
                needs_rag=True,
                needs_business_tools=True,
                rag_query=self._rewrite_rag_query(text, "after_sale_policy"),
                confidence=0.95,
                source="rules",
                intents=["after_sale_policy", "after_sale_eligibility"],
                required_tools=["after_sale_workflow"],
                knowledge_domains=["after_sale_policy"],
                has_realtime_fact=True,
                risk_level="high",
                requires_workflow=True,
                entity_refs=["order_id"],
                fallback_policy="workflow_first",
            )

        # 商品、优惠和通用售后政策是项目默认的稳定复合场景，保持主 intent 与截图一致。
        if signals["product"]:
            required_tools = ["search_products"]
            if signals["realtime"] or signals["promotion"]:
                required_tools.append("get_product_inventory")
            if signals["promotion"]:
                required_tools.append("get_current_user_coupons")

            intents = ["product_consult"]
            if signals["realtime"]:
                intents.append("check_product_realtime_fact")
            if signals["promotion"]:
                intents.append("check_member_promotion")
            if signals["after_sale"]:
                intents.append("after_sale_policy")

            knowledge_domains = ["product_guide"]
            if signals["promotion"]:
                knowledge_domains.append("promotion_policy")
            if signals["after_sale"]:
                knowledge_domains.append("after_sale_policy")

            return self._decision(
                intent="product_availability_and_promotion" if signals["promotion"] else "product_consult",
                needs_rag=True,
                needs_business_tools=True,
                rag_query=self._rewrite_rag_query(text, "product_consult"),
                confidence=0.9,
                source="rules",
                intents=intents,
                required_tools=list(dict.fromkeys(required_tools)),
                knowledge_domains=list(dict.fromkeys(knowledge_domains)),
                required_context=["runtime_context"] if signals["promotion"] else [],
                has_realtime_fact=signals["realtime"] or signals["promotion"],
                risk_level="medium" if signals["after_sale"] else "low",
                fallback_policy="tool_first_then_policy_caveat",
            )

        active_domains = [name for name in ("logistics", "after_sale", "invoice", "promotion") if signals[name]]
        if len(active_domains) > 1:
            domains = []
            if signals["after_sale"]:
                domains.append("after_sale_policy")
            if signals["invoice"]:
                domains.append("invoice_policy")
            if signals["promotion"]:
                domains.append("promotion_policy")
            return self._decision(
                intent="general",
                needs_rag=bool(domains),
                needs_business_tools=signals["logistics"] or signals["promotion"],
                rag_query=text,
                confidence=0.65,
                source="rules",
                intents=active_domains,
                knowledge_domains=domains,
                has_realtime_fact=signals["logistics"] or signals["promotion"],
                risk_level="medium" if signals["after_sale"] else "low",
                fallback_policy="clarify_or_model_plan",
            )

        if signals["logistics"]:
            return self._decision(
                intent="order_logistics",
                needs_rag=False,
                needs_business_tools=True,
                rag_query=text,
                confidence=0.95 if has_order_id else 0.9,
                source="rules",
                intents=["order_logistics"],
                required_tools=["get_order_logistics"],
                has_realtime_fact=True,
                entity_refs=["order_id"] if has_order_id else [],
                fallback_policy="tool_first",
            )

        if signals["after_sale"]:
            return self._decision(
                intent="after_sale_policy",
                needs_rag=True,
                needs_business_tools=False,
                rag_query=self._rewrite_rag_query(text, "after_sale_policy"),
                confidence=0.9,
                source="rules",
                intents=["after_sale_policy"],
                required_tools=[],
                knowledge_domains=["after_sale_policy"],
                has_realtime_fact=False,
                risk_level="medium",
                requires_workflow=False,
                entity_refs=[],
                fallback_policy="knowledge_only",
            )

        if signals["invoice"]:
            return self._decision(
                intent="invoice_policy",
                needs_rag=True,
                needs_business_tools=False,
                rag_query=self._rewrite_rag_query(text, "invoice_policy"),
                confidence=0.95,
                source="rules",
                intents=["invoice_policy"],
                knowledge_domains=["invoice_policy"],
                fallback_policy="knowledge_only",
            )

        if signals["promotion"]:
            return self._decision(
                intent="promotion_faq",
                needs_rag=True,
                needs_business_tools=True,
                rag_query=self._rewrite_rag_query(text, "promotion_faq"),
                confidence=0.9,
                source="rules",
                intents=["promotion_policy"],
                required_tools=["get_current_user_coupons"],
                knowledge_domains=["promotion_policy"],
                required_context=["runtime_context"],
                has_realtime_fact=True,
                fallback_policy="tool_first_then_policy_caveat",
            )

        if "规则" in text:
            return self._decision(
                intent="promotion_faq",
                needs_rag=True,
                needs_business_tools=False,
                rag_query=self._rewrite_rag_query(text, "promotion_faq"),
                confidence=0.7,
                source="rules",
                intents=["promotion_policy"],
                knowledge_domains=["promotion_policy"],
                fallback_policy="knowledge_only",
            )

        return self._decision(
            intent="general",
            needs_rag=False,
            needs_business_tools=False,
            rag_query=text,
            confidence=0.45,
            source="rules_fallback",
            intents=["general"],
        )

    @classmethod
    def _route_signals(cls, text: str) -> dict[str, bool]:
        """收集复合问题中的路由信号，避免第一个关键词提前结束整轮判断。"""

        active_text = cls.NEGATED_AFTER_SALE_PATTERN.sub("", text)
        product = any(keyword in active_text for keyword in ("耳机", "音箱", "充电器", "商品", "产品", "推荐"))
        promotion = any(keyword in active_text for keyword in ("优惠", "活动", "满减", "优惠券", "会员券", "会员", "折扣"))
        realtime = promotion or any(keyword in active_text for keyword in ("库存", "有货", "价格", "多少钱", "现价"))
        after_sale = any(
            keyword in active_text
            for keyword in ("退款", "退货", "售后", "取消订单", "补偿", "退钱", "不合适", "不想要", "还能退", "能退吗")
        )
        return {
            "product": product,
            "promotion": promotion,
            "realtime": realtime,
            "after_sale": after_sale,
            "logistics": any(keyword in active_text for keyword in ("物流", "快递", "配送到哪", "到哪了")),
            "invoice": any(keyword in active_text for keyword in ("发票", "抬头", "开票", "票据")),
        }

    def _apply_safety_constraints(self, user_message: str, route_plan: RoutePlan) -> RoutePlan:
        """对规则和模型结果统一执行高风险最终覆盖，模型不能把写动作降成轻路径。"""

        signals = self._route_signals(user_message)
        has_order_id = bool(self.ORDER_ID_PATTERN.search(user_message))
        high_risk = (
            (signals["after_sale"] and has_order_id)
            or route_plan.risk_level == "high"
            or route_plan.requires_workflow
        )
        if not high_risk:
            return route_plan

        intents = list(dict.fromkeys(["after_sale_policy", "after_sale_eligibility", *route_plan.intents]))
        domains = list(dict.fromkeys(["after_sale_policy", *route_plan.knowledge_domains]))
        entity_refs = list(route_plan.entity_refs)
        if has_order_id:
            entity_refs = list(dict.fromkeys(["order_id", *entity_refs]))
        return route_plan.model_copy(
            update={
                "intent": "after_sale_policy",
                "needs_rag": True,
                "needs_business_tools": True,
                "rag_query": self._rewrite_rag_query(user_message, "after_sale_policy"),
                "confidence": max(route_plan.confidence, 0.95),
                "intents": intents,
                "entity_refs": entity_refs,
                "required_tools": ["after_sale_workflow"],
                "knowledge_domains": domains,
                "has_realtime_fact": route_plan.has_realtime_fact or has_order_id,
                "risk_level": "high",
                "requires_workflow": True,
                "fallback_policy": "workflow_first",
            }
        )

    def _constrain_required_tools(self, route_plan: RoutePlan, candidates: list[ToolCandidate]) -> RoutePlan:
        """用工具目录白名单收窄 required_tools，避免模型开放未知工具。"""
        allowed = self.tool_catalog.allowed_required_tools(route_plan.required_tools, candidates)
        needs_business_tools = bool(allowed) or route_plan.has_realtime_fact or route_plan.requires_workflow
        if allowed == route_plan.required_tools and needs_business_tools == route_plan.needs_business_tools:
            return route_plan
        return route_plan.model_copy(
            update={"required_tools": allowed, "needs_business_tools": needs_business_tools}
        )

    @staticmethod
    def _decision(
        *,
        intent: str,
        needs_rag: bool,
        needs_business_tools: bool,
        rag_query: str,
        confidence: float,
        source: RouteSource,
        intents: list[str],
        entity_refs: list[str] | None = None,
        required_context: list[str] | None = None,
        required_tools: list[str] | None = None,
        knowledge_domains: list[str] | None = None,
        has_realtime_fact: bool = False,
        risk_level: RiskLevel = "low",
        requires_workflow: bool = False,
        fallback_policy: str = "default",
    ) -> RoutePlan:
        """生成规则命中的 PlannerDecision，集中记录意图、风险和公开原因。"""
        return RoutePlan(
            intent=intent,
            needs_rag=needs_rag,
            needs_business_tools=needs_business_tools or bool(required_tools) or has_realtime_fact,
            rag_query=rag_query,
            confidence=confidence,
            source=source,
            intents=intents,
            entity_refs=entity_refs or [],
            required_context=required_context or [],
            required_tools=required_tools or [],
            knowledge_domains=knowledge_domains or [],
            has_realtime_fact=has_realtime_fact,
            risk_level=risk_level,
            requires_workflow=requires_workflow,
            fallback_policy=fallback_policy,
        )

    @staticmethod
    def _rewrite_rag_query(text: str, intent: str) -> str:
        """为 RAG 检索生成更稳定的查询词，不改变用户原始诉求。"""
        parts = [text]
        if intent == "after_sale_policy":
            parts.append("古河商城售后退款退货政策 高风险动作边界")
        elif intent == "product_consult":
            parts.append("古河商城商品知识 库存 会员优惠 活动规则")
        elif intent == "invoice_policy":
            parts.append("电子发票 开票 抬头 税号 修改规则")
        elif intent == "promotion_faq":
            parts.append("会员优惠 满减 优惠券 活动规则")
        return " ".join(dict.fromkeys(parts))

    @staticmethod
    def _public_reason(route_plan: RoutePlan) -> str:
        """把路由原因改写成可展示摘要，避免输出内部策略细节。"""
        if route_plan.requires_workflow:
            return "本轮命中高风险售后路径，只返回 workflow 路由信号，不在轻路径执行写动作。"
        if route_plan.needs_rag and route_plan.needs_business_tools:
            return "本轮同时需要知识依据和实时业务事实，走 Tool + RAG 路由。"
        if route_plan.needs_business_tools:
            return "本轮需要实时业务事实，先收窄到对应只读工具候选。"
        if route_plan.needs_rag:
            return "本轮属于稳定规则咨询，走 RAG 知识路径。"
        return "本轮没有命中业务知识或工具路径，按普通客服对话收口。"
