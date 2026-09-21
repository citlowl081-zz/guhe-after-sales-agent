"""Step 22：运行时上下文整理。这里把页面带入的当前用户订单转成工具可用的安全事实。"""

from __future__ import annotations

import re
from typing import Any

from api.schemas import *
