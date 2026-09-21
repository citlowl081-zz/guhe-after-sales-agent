"""Cursor Shell 安全 Hook 回归测试。"""

from __future__ import annotations

import json
import subprocess
import sys
import unittest
from pathlib import Path


HOOK_PATH = Path(__file__).resolve().parents[1] / "guard-sensitive-shell.py"


def hook_permission(command: str) -> str:
    completed = subprocess.run(
        [sys.executable, str(HOOK_PATH)],
        input=json.dumps({"command": command}, ensure_ascii=False),
        capture_output=True,
        check=True,
        text=True,
    )
    return json.loads(completed.stdout)["permission"]


class GuardSensitiveShellTests(unittest.TestCase):
    def test_allows_read_only_git_status(self) -> None:
        self.assertEqual("allow", hook_permission("git status --short"))

    def test_asks_before_git_write_with_global_directory_option(self) -> None:
        self.assertEqual(
            "ask",
            hook_permission("git -C '/tmp/guhe-project' add README.md"),
        )

    def test_asks_before_branch_or_config_mutation(self) -> None:
        self.assertEqual("ask", hook_permission("git switch -c publish"))
        self.assertEqual("ask", hook_permission("git config user.name tester"))

    def test_denies_direct_local_environment_access(self) -> None:
        self.assertEqual("deny", hook_permission("cat .env"))
        self.assertEqual("allow", hook_permission("cat .env.example"))


if __name__ == "__main__":
    unittest.main()
