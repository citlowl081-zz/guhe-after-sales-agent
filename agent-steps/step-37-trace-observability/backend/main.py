"""Step 37 后端入口。"""

from __future__ import annotations

import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from guhe_runtime.guhe_logging import configure_guhe_logging, guhe_access_log_enabled
configure_guhe_logging(Path(__file__).resolve().parent)

import uvicorn

from api.routes import app


if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, access_log=guhe_access_log_enabled(), log_config=None, reload=True)
