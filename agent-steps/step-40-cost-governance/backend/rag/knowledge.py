"""项目内稳定知识和政策片段。它给 RAG、Trace、Eval 和 Cost 提供同一套依据。"""

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

REFUND_POLICY = Citation(
    source="knowledge/after_sale_policy.md",
    title="古河商城公司未发货退款 SOP",
    snippet="已支付且未出库、未发货的订单，可以发起退款申请；资金类动作必须进入售后审批。",
    score=0.92,
    retrieval_stage="pre_retrieval",
    metadata={"policy_id": "refund_before_shipping", "scene_key": "after_sale_refund"},
)

INVOICE_FAQ = Citation(
    source="knowledge/payment_invoice_policy.md",
    title="古河商城公司发票 FAQ",
    snippet="电子发票通常在订单完成后 24 小时内开具，可在订单详情页查看和下载。",
    score=0.9,
    retrieval_stage="pre_retrieval",
    metadata={"policy_id": "invoice_issue", "scene_key": "payment_invoice"},
)
