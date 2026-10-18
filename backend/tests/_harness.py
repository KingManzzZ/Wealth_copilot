"""通用测试 harness（标准库实现，不依赖 pytest）。

## 为什么又写一个 runner

`tests/rag/run_tests.py` 是仓库里第一套 runner，功能完整但**把测试目录写死**在自己
模块里（`TESTS_DIR` 常量 + `TEST_MODULES` 列表），只能服务 RAG 那一套。
新增的取消链路测试需要**同样的语义**（`check_*` 发现、`Skip`、四态汇总、`-k/--only`），
所以把通用部分抽到这里，由各套件的 `run_tests.py` 传参调用。

**没有反过来改 `tests/rag/run_tests.py`**：那套有 120 多个用例，改 runner 属于
"为了整洁去碰稳定代码"，收益不抵风险。等下次动 RAG 测试时再迁移即可——两套
runner 的对外行为完全一致，迁移是机械的。

## 约定（与 RAG 那套保持一致）

- 测试文件名 `test_*.py`，用例函数名 `check_*`；
- `raise Skip("原因")` 跳过；断言失败抛 `Failure`（AssertionError 子类）；
- 同时兼容 pytest：`Skip` 继承 `unittest.SkipTest`。

用法（在 `backend/` 下）::

    python tests/cancel/run_tests.py
    python tests/cancel/run_tests.py --list
    python tests/cancel/run_tests.py -k cancel
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
import time
import traceback
import unittest
from dataclasses import dataclass, field
from pathlib import Path


# ======================================================================
# 断言工具（测试文件 import 这些）
# ======================================================================


class Skip(unittest.SkipTest):
    """在用例内或模块级 ``raise Skip("原因")`` 可跳过。"""


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
# 执行模型
# ======================================================================


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

    def add(self, result: Result) -> None:
        self.results.append(result)

    @property
    def failures(self) -> list[Result]:
        return [r for r in self.results if r.status in ("fail", "error")]

    def summary(self) -> dict:
        out = {"pass": 0, "fail": 0, "skip": 0, "error": 0}
        for result in self.results:
            out[result.status] = out.get(result.status, 0) + 1
        return out


def _load_module(path: Path, module_key: str):
    spec = importlib.util.spec_from_file_location(module_key, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"无法加载测试模块：{path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_key] = module
    spec.loader.exec_module(module)
    return module


def _is_skip(exc: BaseException) -> bool:
    """跳过判定。

    正常路径是 ``isinstance(exc, Skip)``。但测试文件若因为模块被加载两次而拿到
    另一个 ``Skip`` 类，类身份比较会失效，"跳过"会被误报成"错误"——所以再加一条
    "类名 + SkipTest 基类"的兜底判定。
    """
    if isinstance(exc, unittest.SkipTest):
        return True
    runner = sys.modules.get("run_tests")
    skip_cls = getattr(runner, "Skip", Skip)
    return isinstance(exc, skip_cls) or type(exc).__name__ == "Skip"


def run(
    tests_dir: Path,
    selected: list[str],
    keyword: str | None = None,
    verbose: bool = False,
    module_key_prefix: str = "tests",
) -> Report:
    if str(tests_dir) not in sys.path:
        sys.path.insert(0, str(tests_dir))

    report = Report()

    for filename in selected:
        path = tests_dir / filename
        if not path.exists():
            report.add(
                Result(Case(filename, "<module>", None), "skip", f"文件不存在：{path}")
            )
            continue

        module_key = f"{module_key_prefix}_{path.stem}"
        try:
            module = _load_module(path, module_key)
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
        for attr in sorted(dir(module)):
            if not attr.startswith("check_"):
                continue
            fn = getattr(module, attr)
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
            report.add(Result(case, status, detail, time.perf_counter() - started))
            if verbose or status in ("fail", "error"):
                _print_result(report.results[-1])

    return report


_GLYPH = {"pass": "[ok]  ", "fail": "[FAIL]", "skip": "[skip]", "error": "[ERR] "}
_COLOR = {
    "pass": "\033[32m",
    "fail": "\033[31m",
    "skip": "\033[33m",
    "error": "\033[31m",
}
_RESET = "\033[0m"


def _print_result(result: Result) -> None:
    color = _COLOR.get(result.status, "") if sys.stdout.isatty() else ""
    reset = _RESET if sys.stdout.isatty() else ""
    label = f"{result.case.module}::{result.case.name}"
    print(f"{color}{_GLYPH[result.status]}{reset} {label}  ({result.seconds * 1000:.0f} ms)")
    if result.detail:
        for line in result.detail.rstrip().splitlines():
            print(f"        {line}")


def main(
    tests_dir: Path,
    test_modules: list[str],
    title: str,
    *,
    subtitle_lines: list[str] | None = None,
    module_key_prefix: str = "tests",
) -> int:
    parser = argparse.ArgumentParser(description=f"{title}（标准库 runner）")
    parser.add_argument("--list", action="store_true", help="只列出用例，不执行")
    parser.add_argument("-k", "--keyword", default=None, help="只跑名字含该关键字的用例")
    parser.add_argument("--verbose", action="store_true", help="每个用例都打印")
    parser.add_argument("--only", nargs="*", default=None, help="只跑指定测试文件")
    args = parser.parse_args()

    selected = args.only or test_modules
    if args.list:
        for filename in selected:
            print(filename)
        return 0

    print("=" * 72)
    print(title)
    print(f"  目录 : {tests_dir}")
    for line in subtitle_lines or []:
        print(f"  {line}")
    print("=" * 72)

    report = run(tests_dir, selected, args.keyword, args.verbose, module_key_prefix)

    summary = report.summary()
    print("-" * 72)
    print(
        f"结果：通过 {summary.get('pass', 0)} / 失败 {summary.get('fail', 0)} / "
        f"错误 {summary.get('error', 0)} / 跳过 {summary.get('skip', 0)}"
    )

    if report.failures:
        print("\n失败详情：")
        for result in report.failures:
            print(f"\n  ✗ {result.case.module}::{result.case.name}")
            for line in result.detail.rstrip().splitlines():
                print(f"      {line}")
        return 1

    print("\n✅ 全部通过")
    return 0
