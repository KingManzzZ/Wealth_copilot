"""第 1 层｜RAG 检索工具**入口**：唯一的对外工具 :func:`search_knowledge`。

本模块只做三件事：**参数防御 → 转交引擎 → 返回载荷**。
业务逻辑在 ``impl.py``（引擎）与 ``evidence.py``（适配层）。

**why 分成 ``tools.py`` 而不是直接写在 ``__init__.py``**

`tools_route` 的扫描器按「函数名 → 实现」建索引，用
``func.__module__`` 判断函数是否来自工具层内部。把实现放在叶子模块里，
这个判断最直接（不需要依赖"re-export 的函数也算"这种间接规则）。

**职责边界**：本工具只负责「检索 + 把证据整理成给 LLM 的形态」。
「要不要检索」「检索到之后怎么答」属于 Agent 编排层（`core/agent/`）。
"""

from __future__ import annotations

from . import impl


def search_knowledge(
    query: str,
    top_k: int = 5,
    include_parent: bool = False,
) -> dict:
    """检索金融知识库，返回带来源编号的证据片段。

    适用于：政策法规的具体条款与数额、金融产品的条款与规则、专业概念与方法的权威解释。

    不适用于（请改用对应的数据工具或直接回答）：
    - 用户自己的资产 / 负债 / 流水 / 目标 —— 用 get_assets / get_debts / get_transactions / get_goals
    - 纯数学计算 —— 用 calculate_* 系列工具或直接计算
    - 寒暄、翻译、写代码等与金融知识库无关的请求

    Args:
        query: **可独立检索的完整查询**（不是对话原句）。三点要求：
            1. 追问时必须补全指代 —— 把「它和税优健康险能叠加吗」写成
               「个人养老金与税优健康险能否叠加抵扣」；
            2. 涉及用户的具体数值时，**先用数据工具取得数值再写进查询** ——
               把「按我的收入」写成「年收入 30 万元」，否则「我的收入」在知识库中
               没有任何对应内容，召回质量会很差；
            3. 保持问题原意，不要自行添加知识库里可能没有的前提。
        top_k: 返回证据条数，默认 5。多数情况下 3~5 条足够；
            需要横向对比多个来源时才调大。
        include_parent: 是否附上每条证据所属「节」的全文（默认 False）。
            体积较大，仅在单条证据明显被截断、需要更多上下文时才开启。

    Returns:
        可直接 JSON 序列化的证据载荷，关键字段：

        - ``evidence_quality``: ``high`` / ``medium`` / ``low``
        - ``directive``: ``answer`` / ``answer_with_caveat`` / ``abstain``
        - ``results``: 证据列表，每条含 ``index``（引用编号，如 ``[1]``）、
          ``citation``（文档 | 页码 | 标题路径）、``text``（正文）
        - ``notice``: 需要在回答中遵守的提示（如拒答要求）
        - ``error``: 仅在检索不可用时出现

        注意：``directive`` 为 ``abstain`` 时**必须**如实告知用户未找到依据，
        不得凭记忆补出政策条款或具体数额。
    """
    if not query or not query.strip():
        return {
            "error": "query 为空：请传入可独立检索的完整查询",
            "evidence_quality": "low",
            "directive": "abstain",
            "results": [],
        }

    # 参数防御：模型可能传入字符串形式的数字或越界值
    try:
        k = int(top_k)
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(k, 8))  # 上限 8：超过这个数量对上下文是负担而非帮助

    if isinstance(include_parent, str):
        include_parent = include_parent.strip().lower() in {"1", "true", "yes", "是"}

    return impl.get_engine().search_knowledge(
        query=query.strip(),
        top_k=k,
        include_parent=bool(include_parent),
    )


__all__ = ["search_knowledge"]
