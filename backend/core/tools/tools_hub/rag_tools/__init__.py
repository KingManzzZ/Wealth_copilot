"""RAG 检索工具子包（三层：入口 / 引擎 / 适配层）。

```
rag_tools/
├── __init__.py     ← 第 1 层：唯一的对外工具 search_knowledge
├── impl.py         ← 第 2 层：引擎（单例 Retriever / 预热 / 线程锁 / 异常翻译 / 预算裁剪）
└── evidence.py     ← 第 3 层：适配层（SearchResult → 带编号的证据块 + 三档质量）
```

**为什么收进子包而不是散在 tools_hub 顶层**

`tools_route` 的注册表已改为**以 `tools_schema.py` 为唯一依据**，因此文件怎么放
**不再影响注册表内容**——本子包里的函数（`EvidenceItem`、`classify_quality`、
`build_evidence_payload` 等）都不会被注册成工具，因为它们不在 schema 里。

收进子包的真实收益是**分层清晰**：一个目录看全 RAG 检索能力，
且内部三个文件的相互引用都是包内相对导入，不牵扯 hub 顶层。

**注册表发现机制（重要）**

`tools_route._discover_implementations()` 会**递归**扫描 hub（含子包），
按「函数名 → 实现」建索引，再按 schema 收敛。因此第 1 层的
``search_knowledge`` 无论定义在本文件的哪个子模块里都能被找到。

**对外只暴露一个名字**：

    from core.tools.tools_hub.rag_tools import search_knowledge
"""

from __future__ import annotations

from .tools import search_knowledge

__all__ = ["search_knowledge"]
