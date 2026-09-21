"""Step 23：降级策略。工具失败和模型不可用时从这里收口。"""

from __future__ import annotations

from api.schemas import *

def fallback_answer(category: ErrorCategory) -> str:
    """返回降级场景的安全话术。"""
    templates = {
        "timeout": "物流工具暂时超时，我现在不能编造包裹位置。你可以稍后重试，或转人工客服继续核验。",
        "model_unavailable": "模型服务暂时不可用，但工具链路已完成核验。当前先用模板口径说明查询结果。",
        "high_risk_write_blocked": "退款、取消和补偿都属于高风险操作，我不能自动执行，会建议转人工客服处理。",
    }
    return templates.get(category, "系统暂时没有拿到可靠结果，我不会编造业务事实。")
