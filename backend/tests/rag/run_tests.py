"""轻量测试 runner（标准库实现，不依赖 pytest）。

**为什么不用 pytest**

本项目的依赖统一声明在仓库根 `requirements.txt`（`backend/` 下没有自己的依赖清单），
其中**不含 pytest**。用标准库实现一个 runner，保证
**`python tests/rag/run_tests.py` 在任何 Python 3.10+ 环境下都能跑**——
测试框架本身不该成为运行测试的门槛。

同时**兼容 pytest**：测试文件用 `test_*` 命名、`check_*` 函数不需要 fixture，
`pytest tests/rag/` 也能收集执行（但需配置 ``python_functions = check_*``，
否则 pytest 默认只认 ``test*`` 开头的函数，会收集到 0 个用例）。

用法::

    python tests/rag/run_tests.py              # 跑全部（真实模型测试默认跳过）
    python tests/rag/run_tests.py --list       # 只列出用例
    python tests/rag/run_tests.py -k evidence  # 只跑名字含 evidence 的
    RAG_TEST_REAL=1 python tests/rag/run_tests.py   # 含真实模型/知识库测试
    python tests/rag/run_tests.py --verbose    # 每个用例都打印
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sys
import time
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parents[1]  # tests/rag/ → tests/ → backend/

# 测试文件用 `from run_tests import ...` 取断言工具。用 importlib 加载时模块名可能是
# rag tests_run_tests，因此这里把两个名字都指向本模块，避免被重复加载成两个实例
# （重复加载会导致 raises/Failure 不是同一个类，异常判定失效）。
sys.modules.setdefault("run_tests", sys.modules[__name__])

# 测试文件还 import 同目录的 _support。把它所在的目录（而非 _support 本身）放进
# sys.path，保证无论用 runner 还是 pytest 都只会有**一份** _support 模块实例。
if str(TESTS_DIR) not in sys.path:
    sys.path.insert(0, str(TESTS_DIR))

# 测试文件按依赖成本从低到高排列：纯逻辑在前，需要模型/网络的在后
TEST_MODULES = [
    "test_thresholds.py",  # 纯数学，无依赖
    "test_evidence.py",  # 纯逻辑（证据适配层）
    "test_engine.py",  # 引擎逻辑（注入替身，无模型）
    "test_calibration_eval.py",  # 评估口径一致性 + 纳入 eval 自检
    "test_tool_contract.py",  # 工具注册契约（需 tools_route，会触发数据库）
    "test_context.py",  # 历史回放与上下文裁剪（纯逻辑，无模型）
    "test_l1.py",  # L1 单次调用：稳定前缀 / 工具结果截断 / 度量
    "test_agent_events.py",  # Agent SSE 事件（需 openai + 假客户端）
    "test_api_e2e.py",  # 端到端 /api/chat（需 fastapi TestClient + 假 LLM）
    "test_real_rag.py",  # 真实模型 + 知识库（默认跳过）
    "test_real_agent.py",  # 真实 Agent 端到端（默认跳过）
]


@dataclass
class Case:
    module: str
    name: str
    fn: object


@dataclass
class Result:
    case: Case
    status: str  # pass | fail | skip | error
    detail: str = ""
    seconds: float = 0.0


@dataclass
class Report:
    results: list[Result] = field(default_factory=list)

    def add(self, r: Result) -> None:
        self.results.append(r)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status in ("fail", "error")]

    def summary(self) -> dict:
        out = {"pass": 0, "fail": 0, "skip": 0, "error": 0}
        for r in self.results:
            out[r.status] = out.get(r.status, 0) + 1
        return out


# ======================================================================
# 断言工具（测试文件 import 这些）
# ======================================================================


class Skip(unittest.SkipTest):
    """在用例内或模块级 ``raise Skip("原因")`` 可跳过。

    继承 ``unittest.SkipTest``（而非裸 Exception）是为了**同时兼容 pytest**：
    pytest 原生识别 ``SkipTest``，因此模块级跳过在 pytest 下也会被正确报告为
    skipped 而不是 error。标准库自带，不引入依赖。
    """


class Failure(AssertionError):
    """断言失败。"""


def eq(actual, expected, msg: str = "") -> None:
    if actual != expected:
        raise Failure(f"{msg}：期望 {expected!r}，实际 {actual!r}")


def close(actual, expected, tol: float = 1e-6, msg: str = "") -> None:
    if actual is None or abs(actual - expected) > tol:
        raise Failure(f"{msg}：期望 ≈{expected}（±{tol}），实际 {actual!r}")


def true(cond, msg: str = "断言不成立") -> None:
    if not cond:
        raise Failure(msg)


def in_(needle, haystack, msg: str = "") -> None:
    if needle not in haystack:
        raise Failure(f"{msg}：{needle!r} 不在期望的容器中")


def not_in(needle, haystack, msg: str = "") -> None:
    if needle in haystack:
        raise Failure(f"{msg}：{needle!r} 不应出现")


def raises(exc_type, fn, msg: str = "") -> Exception:
    try:
        fn()
    except exc_type as exc:
        return exc
    except Exception as exc:  # noqa: BLE001
        raise Failure(f"{msg}：期望 {exc_type.__name__}，实际 {type(exc).__name__}: {exc}")
    raise Failure(f"{msg}：期望抛 {exc_type.__name__}，但没有抛异常")


# ======================================================================
# 加载与执行
# ======================================================================


def _load_module(path: Path, module_key: str):
    spec = importlib.util.spec_from_file_location(module_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载测试模块：{path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = mod
    spec.loader.exec_module(mod)
    return mod


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _resolve_skip_class():
    """返回测试文件实际用的那个 ``Skip`` 类。

    正常情况下就是本模块的 ``Skip``。但若测试文件导入的是另一个 ``run_tests``
    实例（模块被加载两次的极端情形），``except Skip`` 会因类身份不同而漏捕，
    于是"跳过"会被误报成"错误"。这里用 **类名 + unittest.SkipTest 基类** 做兜底判定，
    让跳过在任何加载路径下都能被正确识别。
    """
    return sys.modules.get("run_tests", sys.modules[__name__]).Skip


def _is_skip(exc: BaseException) -> bool:
    if isinstance(exc, unittest.SkipTest):
        return True
    skip_cls = _resolve_skip_class()
    return isinstance(exc, skip_cls) or type(exc).__name__ == "Skip"


def run(selected: list[str], keyword: str | None, verbose: bool) -> Report:
    report = Report()

    for filename in selected:
        path = TESTS_DIR / filename
        if not path.exists():
            report.add(
                Result(Case(filename, "<module>", None), "skip", f"文件不存在：{path}")
            )
            continue

        module_key = f"ragtests_{path.stem}"
        try:
            mod = _load_module(path, module_key)
        except BaseException as exc:  # noqa: BLE001
            if _is_skip(exc):
                report.add(Result(Case(filename, "<module>", None), "skip", str(exc)))
            else:
                report.add(
                    Result(
                        Case(filename, "<import>", None),
                        "error",
                        f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}",
                    )
                )
            continue

        cases: list[Case] = []
        for attr in sorted(dir(mod)):
            if not attr.startswith("check_"):
                continue
            fn = getattr(mod, attr)
            if not callable(fn):
                continue
            name = f"{filename}::{attr[6:]}"
            if keyword and keyword.lower() not in name.lower():
                continue
            cases.append(Case(filename, attr[6:], fn))

        if not cases:
            report.add(Result(Case(filename, "<cases>", None), "skip", "没有匹配的用例"))
            continue

        for case in cases:
            started = time.perf_counter()
            # 先清空缓冲再执行：被测代码可能直接往 stdout 写字（例如 rich 的表格，
            # 它绕过 print 的缓冲），不 flush 的话这些输出会跑到用例结论后面，
            # 看起来像是"结论先出、细节后出"。刷一次让顺序符合直觉。
            sys.stdout.flush()
            try:
                case.fn()
                status, detail = "pass", ""
            except BaseException as exc:  # noqa: BLE001
                if _is_skip(exc):
                    status, detail = "skip", str(exc)
                else:
                    is_fail = (
                        isinstance(exc, Failure)
                        or isinstance(exc, AssertionError)
                        or exc.__class__.__name__ == "Failed"
                    )
                    status = "fail" if is_fail else "error"
                    detail = f"{type(exc).__name__}: {exc}"
                    if status == "error":
                        detail += f"\n{traceback.format_exc()}"
            finally:
                sys.stdout.flush()
            elapsed = time.perf_counter() - started
            report.add(Result(case, status, detail, elapsed))
            if verbose or status in ("fail", "error"):
                _print_result(report.results[-1])

    return report


_GLYPH = {"pass": "[ok]  ", "fail": "[FAIL]", "skip": "[skip]", "error": "[ERR] "}
_COLOR = {"pass": "\033[32m", "fail": "\033[31m", "skip": "\033[33m", "error": "\033[31m"}
_RESET = "\033[0m"


def _print_result(r: Result) -> None:
    color = _COLOR.get(r.status, "") if sys.stdout.isatty() else ""
    reset = _RESET if sys.stdout.isatty() else ""
    label = f"{r.case.module}::{r.case.name}"
    print(f"{color}{_GLYPH[r.status]}{reset} {label}  ({r.seconds * 1000:.0f} ms)")
    if r.detail:
        for line in r.detail.rstrip().splitlines():
            print(f"        {line}")


def main() -> int:
    parser = argparse.ArgumentParser(description="RAG 融合测试框架（标准库 runner）")
    parser.add_argument("--list", action="store_true", help="只列出用例，不执行")
    parser.add_argument("-k", "--keyword", default=None, help="只跑名字含该关键字的用例")
    parser.add_argument("--verbose", action="store_true", help="每个用例都打印")
    parser.add_argument(
        "--only", nargs="*", default=None, help="只跑指定的测试文件（文件名）"
    )
    args = parser.parse_args()

    selected = args.only or TEST_MODULES
    if args.list:
        for f in selected:
            print(f)
        return 0

    print("=" * 72)
    print("RAG 融合测试")
    print(f"  backend : {BACKEND_DIR}")
    print(f"  真实模型 : {'启用' if _env_flag('RAG_TEST_REAL') else '跳过（设 RAG_TEST_REAL=1 启用）'}")
    print("=" * 72)

    report = run(selected, args.keyword, args.verbose)

    s = report.summary()
    print("-" * 72)
    print(
        f"结果：通过 {s.get('pass', 0)} / 失败 {s.get('fail', 0)} / "
        f"错误 {s.get('error', 0)} / 跳过 {s.get('skip', 0)}"
    )
    slow = sorted(report.results, key=lambda r: -r.seconds)[:3]
    if slow and slow[0].seconds > 0.5:
        print("最慢：" + "，".join(f"{r.case.name} {r.seconds:.2f}s" for r in slow))

    if report.failures:
        print("\n失败详情：")
        for r in report.failures:
            print(f"\n  ✗ {r.case.module}::{r.case.name}")
            for line in r.detail.rstrip().splitlines():
                print(f"      {line}")
        return 1

    print("\n✅ 全部通过")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
