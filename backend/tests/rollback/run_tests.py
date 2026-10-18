"""撤回一轮的测试 runner。

对应调研：[`../../Investigation/单轮次状态回滚/`](../../Investigation/单轮次状态回滚/)

    cd backend
    python tests/rollback/run_tests.py
    python tests/rollback/run_tests.py -k conflict

路由层需要 fastapi + httpx，缺失时整文件跳过。
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TESTS_ROOT = TESTS_DIR.parent
BACKEND_DIR = TESTS_DIR.parents[1]

for path in (str(BACKEND_DIR), str(TESTS_ROOT), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

from _harness import (  # noqa: E402
    Case,
    Failure,
    Report,
    Result,
    Skip,
    close,
    eq,
    in_,
    main,
    not_in,
    raises,
    true,
)

sys.modules.setdefault("run_tests", sys.modules[__name__])

TEST_MODULES = [
    "test_withdraw.py",
    "test_withdraw_route.py",
]


if __name__ == "__main__":
    raise SystemExit(
        main(
            TESTS_DIR,
            TEST_MODULES,
            "撤回一轮：对话 + 数据回滚测试",
            subtitle_lines=[
                "范围：写集差异 / 撤回任意一轮（截断）/ 冲突 / 路由",
                "说明：路由层需要 fastapi + httpx，缺失时该文件整体跳过",
            ],
            module_key_prefix="rollbacktests_",
        )
    )
