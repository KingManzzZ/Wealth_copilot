"""生成中止 / 取消链路测试 runner。

覆盖四层，越往后依赖越重：

1. ``test_cancel_token.py``   —— 取消令牌本身的语义（纯逻辑）
2. ``test_agent_cancel.py``   —— agent 循环的检查点（假 LLM / 假工具）
3. ``test_memory_replay.py``  —— interrupted 轮次的落库与回放（临时数据库）
4. ``test_chat_route_cancel.py`` —— 真 ASGI 路由：409、取消接口、断连探测
   （需要 fastapi + httpx；缺失时自动跳过并说明原因）

用法（在 ``backend/`` 下）::

    python tests/cancel/run_tests.py
    python tests/cancel/run_tests.py --list
    python tests/cancel/run_tests.py -k token
    python tests/cancel/run_tests.py --only test_agent_cancel.py

不依赖 pytest：断言与执行器来自 ``tests/_harness.py``（标准库实现）。
"""

from __future__ import annotations

import sys
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
TESTS_ROOT = TESTS_DIR.parent
BACKEND_DIR = TESTS_DIR.parents[1]

# 测试文件要 import `core.*`（backend 根）。用脚本方式启动时 sys.path[0] 是
# tests/cancel，cwd 不会自动进来，所以这里显式补 backend 根。
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
# harness 在 tests/ 下
if str(TESTS_ROOT) not in sys.path:
    sys.path.insert(0, str(TESTS_ROOT))
# 测试文件 `import _support` 需要本目录在 sys.path
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

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

# 测试文件里 `from run_tests import Skip, eq, ...`：把本模块登记为 run_tests，
# 避免被重复加载成两个实例（两份 Skip 类会让"跳过"判定失效）。
sys.modules.setdefault("run_tests", sys.modules[__name__])

#: 依赖成本从低到高
TEST_MODULES = [
    "test_cancel_token.py",
    "test_agent_cancel.py",
    "test_memory_replay.py",
    "test_chat_route_cancel.py",
]


if __name__ == "__main__":
    raise SystemExit(
        main(
            TESTS_DIR,
            TEST_MODULES,
            "生成中止 / 取消链路测试",
            subtitle_lines=[
                "范围：取消令牌 / agent 检查点 / interrupted 记忆语义 / 路由取消",
                "说明：路由层需要 fastapi + httpx，缺失时该文件整体跳过",
            ],
            module_key_prefix="canceltests_",
        )
    )
