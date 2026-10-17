"""路径定位：``config.yaml`` 与模块根的**唯一锚点**。

## 为什么要单独成模块

``config.py`` 依赖 ``pyyaml``。如果把"配置文件在哪"也写在那里，那么当某个解析器没装时，
连"找不到配置文件"这个判断都会一起失效——而定位配置恰恰是最不该依赖第三方库的一件事。

分离之后：

- 集成层、测试、审计脚本可以只导入本模块做定位；
- 缺 ``pyyaml`` 时的报错出现在"解析内容"这一步，语义清晰。

## 为什么不能按 CWD 定位

``config.yaml`` 首行写明了约定：``paths.*`` 相对**配置文件所在目录**解析。
因此"配置文件本身在哪"必须与 CWD 无关——否则同一份配置在不同目录下执行，
会指向不同的 ``data/`` / ``db/`` / ``models/``，症状是"数据明明存在却读不到"。

历史上多个入口各自实现了一遍 CWD 回退（``cli.py`` 的 ``_existing_or_project_path``、
若干审计脚本的 ``sys.path.insert(0, "src")``），口径并不一致。本模块把它们统一到一处。

## 定位顺序

1. 显式传入的路径（绝对路径直接用；相对路径先按 CWD，再按模块根）
2. 环境变量 ``WEALTH_RAG_CONFIG``（规则同上）
3. 模块根下的 ``config.yaml``（**不依赖 CWD**）

前两档保留"先按 CWD"是为了命令行直觉（``cd backend/RAG_ && python ....py`` 与
``python RAG_/xxx.py --config RAG_/config.yaml`` 都能工作）。
"""

from __future__ import annotations

import os
from pathlib import Path

#: 默认配置文件名
DEFAULT_CONFIG_NAME = "config.yaml"

#: 覆盖配置文件位置的环境变量（集成层与测试共用同一名字）
ENV_CONFIG = "WEALTH_RAG_CONFIG"


def module_root() -> Path:
    """RAG 模块根目录（含 ``config.yaml`` / ``src`` / ``data`` / ``db`` / ``models``）。

    本文件位于 ``RAG_/src/wealth_rag/paths.py``，故 ``parents[2]`` 即 ``RAG_``。
    用 ``__file__`` 推导而非 CWD，保证从任何目录调用结果一致。
    """
    return Path(__file__).resolve().parents[2]


def default_config_path() -> Path:
    """模块根下的 ``config.yaml``（绝对路径）。"""
    return module_root() / DEFAULT_CONFIG_NAME


def _candidates(raw: str | Path) -> list[Path]:
    """把用户给的路径展开成候选列表（去重、绝对化）。"""
    p = Path(str(raw)).expanduser()
    if p.is_absolute():
        return [p]

    out: list[Path] = []
    seen: set[Path] = set()
    for base in (Path.cwd(), module_root()):
        candidate = (base / p).resolve()
        if candidate not in seen:
            seen.add(candidate)
            out.append(candidate)
    return out


def locate_config(config_path: str | Path | None = None) -> tuple[Path | None, list[Path]]:
    """定位配置文件，返回 ``(命中的路径 或 None, 尝试过的路径列表)``。

    不抛异常——调用方可以据此给出更贴合语境的提示（测试要跳过、脚本要报错、
    集成层要降级，三种反应不同）。

    ⚠️ **显式指定路径时不做静默回退**：用户给了 ``--config /tmp/x.yaml``，
    若那里没有文件，就应当失败并说明，而不是悄悄用模块根的默认配置——
    否则"我明明指定了配置"和实际生效的配置不一致，排查成本极高。
    """
    explicit = config_path if config_path not in (None, "") else os.environ.get(ENV_CONFIG)

    if explicit not in (None, ""):
        candidates = _candidates(explicit)
    else:
        candidates = [default_config_path()]

    tried: list[Path] = []
    for candidate in candidates:
        if candidate in tried:
            continue
        tried.append(candidate)
        if candidate.is_file():
            return candidate, tried

    return None, tried


def resolve_config_path(config_path: str | Path | None = None) -> Path:
    """定位配置文件；找不到时抛 ``FileNotFoundError``，并列出所有尝试过的路径。"""
    path, tried = locate_config(config_path)
    if path is not None:
        return path

    lines = "\n".join(f"  - {p}" for p in tried)
    raise FileNotFoundError(
        f"找不到 RAG 配置文件 {DEFAULT_CONFIG_NAME}，已尝试：\n{lines}\n"
        f"可用环境变量 {ENV_CONFIG} 指定，或把 {DEFAULT_CONFIG_NAME} 放到 {module_root()}"
    )
