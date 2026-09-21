"""轻量会话状态。只保存项目演示需要的计数或短期字典。"""

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

MESSAGE_COUNT_BY_SESSION: dict[str, int] = {}

WORKFLOW_CHECKPOINTS: dict[tuple[str, str], dict[str, Any]] = {}

SUBMITTED_ACTIONS: dict[str, dict[str, Any]] = {}
