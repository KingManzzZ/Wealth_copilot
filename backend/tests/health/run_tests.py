"""财务健康度测试 runner。

    cd backend
    python tests/health/run_tests.py
    python tests/health/run_tests.py -k emergency
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

TEST_MODULES = ["test_health.py"]


if __name__ == "__main__":
    raise SystemExit(
        main(
            TESTS_DIR,
            TEST_MODULES,
            "财务健康度 5 维评分测试",
            subtitle_lines=[
                "范围：储蓄 / 应急 / 负债 / 目标 / 资产配置 + 缺失数据 + 字段回退",
                "数据：每个用例一个一次性 SQLite 库",
            ],
            module_key_prefix="healthtests_",
        )
    )
