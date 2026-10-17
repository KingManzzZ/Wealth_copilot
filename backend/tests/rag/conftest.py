"""pytest 配置（自定义 runner 不需要它，但装了 pytest 时也能直接跑）。

作用只有一个：把本目录加进 ``sys.path``，让测试文件能 ``import run_tests``
与 ``import _support``。

> 不让测试文件彼此用包内相对导入，是为了让**两套 runner 都能工作**：
> 自定义 runner 用 importlib 按文件路径加载（没有包上下文），
> pytest 用 rootdir 收集。两者都依赖"本目录在 sys.path 上"这个前提。
"""

import sys
from pathlib import Path

_TESTS_DIR = Path(__file__).resolve().parent
if str(_TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(_TESTS_DIR))
