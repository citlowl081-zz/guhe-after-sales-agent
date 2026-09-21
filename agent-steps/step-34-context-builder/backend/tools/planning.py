"""轻量意图识别和参数提取。复杂执行计划会放到 planner 或 workflow。"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any, Literal, TypedDict

import httpx
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from langgraph.graph import END, StateGraph
from pydantic import BaseModel, Field

from api.schemas import *

def extract_order_id(user_message: str) -> str | None:
    """从用户消息里提取订单号，缺失时让上层进入澄清或记忆消歧。"""
    match = re.search(r"\b(?:SO[A-Za-z0-9_-]{6,}|[A-Za-z0-9][A-Za-z0-9_-]{5,})\b", user_message)
    return match.group(0) if match else None

def classify_intent(user_message: str) -> Intent:
    """用轻量规则识别本轮意图，避免高风险动作直接进入普通回答。"""
    if any(term in user_message for term in ["退款", "退钱", "退货", "取消订单"]):
        return "refund_request"
    if any(term in user_message for term in ["VIP", "会员", "权益"]):
        return "member_query"
    if any(term in user_message for term in ["订单", "物流", "当前订单", "这个订单", "刚才那个"]):
        return "order_query"
    return "general_chat"
