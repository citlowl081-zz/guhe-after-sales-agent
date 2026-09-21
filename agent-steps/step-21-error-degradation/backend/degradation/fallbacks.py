"""Step 21：错误降级策略。工具超时、模型不可用和高风险动作都在这里给稳定兜底。"""

from __future__ import annotations

from api.schemas import *


def compose_model_answer(observation: Observation, user_message: str) -> str:
    """执行 compose_model_answer 对应的项目逻辑。"""
    # 项目演示用故障注入：真实生产系统应通过模型客户端异常或健康状态判断服务不可用。
    if "模型抽风" in user_message:
        raise ModelServiceError("模型服务暂时不可用。")
    if observation.status == "success":
        return f"我通过只读工具查到：{observation.summary}"
    return f"工具没有返回可用事实：{observation.summary}"

def fallback_answer(category: ErrorCategory) -> str:
    """执行 fallback_answer 对应的项目逻辑。"""
    templates = {
        "timeout": "物流工具暂时超时，我现在不能编造包裹位置。你可以稍后重试，或转人工客服继续核验。",
        "validation_error": "当前请求缺少必要参数或格式不正确，请补充正确的订单号或售后信息后再试。",
        "not_found": "我没有查到对应订单或记录，不能凭空补充业务事实。请核对订单号后再发一次。",
        "forbidden": "当前账号无权查看这笔订单，我不能透露其他用户的订单信息。",
        "business_error": "订单当前状态不满足这项操作条件，我会按业务规则说明原因，必要时转人工继续处理。",
        "model_unavailable": "模型服务暂时不可用，但工具链路已完成核验。当前先用模板口径说明查询结果。",
        "system_error": "系统暂时异常，我不会编造结果。你可以稍后重试，或转人工客服继续核验。",
        "high_risk_write_blocked": "退款、取消和补偿都属于高风险操作，我不能自动执行，会建议转人工客服处理。",
    }
    return templates.get(category, "系统暂时没有拿到可靠结果，我不会编造业务事实。")
