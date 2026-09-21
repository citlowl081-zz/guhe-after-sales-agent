"""意图识别、订单号提取和 token 估算等轻量规划工具。"""

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

def classify_intent(user_message: str) -> Intent:
    """用简单可读规则做项目级意图识别，展示路由入口而不隐藏在模型黑箱里。"""
    if any(term in user_message for term in ["系统提示词", "hidden reasoning", "隐藏推理", "工具 schema", "内部策略"]):
        return "security_request"
    if any(term in user_message for term in ["退款", "退钱", "退货", "取消订单"]):
        return "refund_request"
    if any(term in user_message for term in ["订单", "物流", "快递"]):
        return "order_query"
    return "general_chat"

def extract_order_id(user_message: str) -> str | None:
    """从用户问题中抽取单个古河商城订单号，供工具调用前参数校验。"""
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|ORD\d{4,})\b", user_message, flags=re.IGNORECASE)
    return match.group(0) if match else None

def estimate_tokens(text: str) -> int:
    """用近似 token 估算驱动项目里的压缩决策，强调成本和窗口不是玄学。"""
    return max(1, len(text) // 2)
