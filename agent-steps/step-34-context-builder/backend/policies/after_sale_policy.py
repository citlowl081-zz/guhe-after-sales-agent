"""售后政策和高风险资格判断。这里只判断边界，不直接执行退款。"""

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
from tools.runtime_context import *
from tools.tool_runtime import make_tool_call

POLICIES: dict[str, Citation] = {
    "POLICY-REFUND-UNSHIPPED": Citation(
        citation_id="c-refund-unshipped",
        source_title="古河商城公司未发货退款 SOP",
        source_path="knowledge/after_sale_policy.md",
        policy_id="POLICY-REFUND-UNSHIPPED",
        snippet="已支付且未出库、未发货的订单，可以发起退款申请；资金类动作必须进入售后审批。",
    )
}
