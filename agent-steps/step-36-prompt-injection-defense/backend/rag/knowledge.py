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

SAFE_REFUND_POLICY = Citation(
    citation_id="c-refund-unshipped",
    source_title="古河商城公司未发货退款 SOP",
    source_path="knowledge/after_sale_policy.md",
    policy_id="POLICY-REFUND-UNSHIPPED",
    snippet="已支付且未出库、未发货的订单，可以发起退款申请；资金类动作必须进入售后审批。",
)
