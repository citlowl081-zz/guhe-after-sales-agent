"""Step 33 的会话状态。Runtime Context 之后仍要保留高风险 workflow checkpoint。"""

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
MESSAGE_COUNT_BY_SESSION: dict[str, int] = {}

WORKFLOW_CHECKPOINTS: dict[tuple[str, str], dict[str, Any]] = {}

SUBMITTED_ACTIONS: dict[str, dict[str, Any]] = {}
