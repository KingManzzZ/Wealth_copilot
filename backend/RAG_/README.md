# wealth-rag · 个人财富规划 RAG 检索模块

面向**中文金融领域**的 RAG（检索增强生成）检索底座。把专业金融知识（研报、教材、法规、笔记）摄入为可检索的证据库，
在用户以自然语言提问时返回**高相关、可溯源**的证据片段，供后续 LLM 编排阶段拼接回答。

> 本模块**只负责检索**：不包含答案生成、多轮对话编排与 Agent 工具调用——那是下一阶段的事。
> 输出的是排序后的证据块（正文 + 标题面包屑 + 来源 + 页码 + 父块上下文），接口已按编排需求设计。

---

## 特性

- **结构感知切分**：源码级模仿 RAGFlow 的模板化切分设计，以文档标题树为骨架，而非盲目按字数切割
- **中文金融适配**：面包屑注入提升上下文、金融实体保护避免「第 24 | 条」「4.5 | %」被切断、表格文本化参与检索
- **中英文档通用**：切分/检索/重排全链路支持英文文档与中英混合知识库；英文超长段落按句子边界兜底切分，中文查询可直接命中英文文档（跨语言检索）
- **混合检索**：稠密语义（Qwen3-Embedding）+ 稀疏关键词（jieba-BM25）双路召回，RRF 融合
- **两阶段精排**：Qwen3-Reranker 判别式重排（yes/no logit 打分），相关性区分度明确
- **本地小模型**：嵌入与重排模型均本地运行（FP16 合计约 2.5GB），8GB 显存即可，检索环节离线可用
- **零部署向量库**：Qdrant 本地嵌入模式，无需任何服务进程；数据文件可原样迁移至服务模式
- **可溯源证据**：每个结果携带来源文件、页码、标题路径与父块全文
- **库 + CLI 双形态**：既能作为 Python 库被 Agent 直接 `import`，也能用 `rag` 命令行批处理

---

## 架构

```
【摄入管线】（离线批处理）
原始文档 ──► 解析（Docling / PyMuPDF）──► 标准 Markdown + 标题层级
        ──► 六步切分（结构切分→split-then-merge→硬切分兜底→表格行切→父子块→元数据）
        ──► Qwen3-Embedding 批量编码（稠密）+ jieba 词频（稀疏）
        ──► Qdrant 本地库（dense + sparse 双命名向量）

【检索管线】（在线）
中文查询 ──► 查询编码（指令前缀）+ jieba 分词
        ──► 双路召回（dense top-30 + sparse top-30）
        ──► RRF 融合 top-20 ──► Qwen3-Reranker 精排 ──► Top-K 证据块
        ──► 返回：正文 + 标题面包屑 + 来源 + 页码 + 父块上下文
```

### 数据模型

`Chunk` 采用 **mom/text 两级结构**（对齐 RAGFlow TokenChunker 的 `mom`/`text` 设计）：

| 层级 | 内容 | 用途 |
| --- | --- | --- |
| `text` 子块（≤512 tokens） | 检索与嵌入的最小单元 | 参与召回匹配 |
| `mom` 父块（所属「节」全文） | 存于子块元数据，**不单独入库** | 命中后扩展上下文，供 LLM 编排 |

---

## 技术选型

| 环节 | 选型 | 协议 | 运行位置 |
| --- | --- | --- | --- |
| 文档解析 | [Docling](https://github.com/docling-project/docling)（备选 PyMuPDF 快速通道） | MIT | CPU |
| 切分 | 自研（源码级模仿 RAGFlow：结构切分 + `naive_merge` + 父子块） | — | CPU |
| 稠密嵌入 | [Qwen3-Embedding-0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B) | Apache-2.0 | 本地 GPU/CPU |
| 稀疏检索 | jieba 分词 + BM25（Qdrant 稀疏向量 IDF 修正 / rank_bm25 兜底） | MIT | CPU |
| 向量库 | [Qdrant](https://qdrant.tech/) 本地嵌入模式 | Apache-2.0 | 磁盘 |
| 融合 | RRF（Qdrant 原生 `prefetch` + `Fusion.RRF`） | — | — |
| 重排 | [Qwen3-Reranker-0.6B](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B) | Apache-2.0 | 本地 GPU/CPU |
| 计数 | tiktoken（cl100k_base，与 RAGFlow 计数口径一致） | MIT | CPU |
| CLI | Typer + Rich + Pydantic | — | — |

**为什么是这些方案**：解析选 Docling 是因为一个工具统一覆盖 PDF/Office/网页且输出带标题层级的 Markdown（切分的必需输入）；
嵌入/重排选 Qwen3 0.6B 套件是因为该系列位居 MTEB 多语言榜首，而 0.6B 尺寸是 8GB 显存约束下的最优解；
向量库选 Qdrant 本地模式是因为零部署、内建稀疏向量与 RRF，且可平滑升级为服务模式。
完整论证与候选方案对比见 [TECH_SUMMARY.md](./TECH_SUMMARY.md)。

---

> ## ⚠️ 本模块已不再是独立项目
>
> 自 2026-10-15 起，本目录作为 **`backend/` 的子模块**存在，与 WealthPilot Agent 融合：
>
> | 变化 | 说明 |
> | :--- | :--- |
> | **依赖** | 已合并进仓库根的 `requirements.txt`，本目录**不再有** `pyproject.toml` |
> | **安装** | 不需要（也不应）再执行 `pip install -e .`；集成层会在导入前自动把 `src/` 加入 `sys.path` |
> | **调用方式** | Agent 通过工具 `search_knowledge` 调用（`backend/core/tools/tools_hub/`）；本目录的 CLI 与评估脚本仍可独立运行 |
> | **配置** | 仍是本目录下的 [`config.yaml`](./config.yaml)，集成层用绝对路径定位它，可继续用 `WEALTH_RAG_CONFIG` 覆盖 |
>
> **下文「安装」一节保留的是它作为独立项目时的原始说明**，仅用于理解历史；
> 实际环境搭建请以仓库根的 `requirements.txt` 为准。

## 快速开始

### 1. 环境要求

| 项目 | 要求 |
| --- | --- |
| Python | 3.10+（与 Agent 后端一致） |
| GPU（可选） | NVIDIA，≥8GB 显存；无 GPU 时自动用 CPU |
| 磁盘 | ≥8GB（模型约 2.3GB + 依赖） |

### 2. 安装（历史说明，见上方警示）

搭建整个仓库的环境请在**仓库根目录**执行：

```powershell
python -m pip install -r requirements.txt

# GPU 用户需单独装 CUDA 版 torch（PyPI 默认是 CPU 版，重排会慢 10 倍以上）
python -m pip install torch --index-url https://download.pytorch.org/whl/cu128
```

<details>
<summary>折叠：作为独立项目时的原始安装步骤（已失效，仅存档）</summary>

```powershell
# 创建独立环境
conda create -y -n wealth_rag python=3.12

# 安装 PyTorch（GPU 用户；cu128 支持 RTX 40/50 系）
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cu128

# 安装本项目（含 fast/dev 可选依赖）
python -m pip install -e ".[dev,fast]" -i https://pypi.tuna.tsinghua.edu.cn/simple
```

> 仅用 CPU 时可跳过 PyTorch 安装步骤（会由主依赖自动装 CPU 版）。

</details>

### 3. 下载本地模型

```powershell
.\scripts\download_models.ps1
```

脚本从 ModelScope 下载 `Qwen3-Embedding-0.6B` 与 `Qwen3-Reranker-0.6B` 到 `models/`，会被自动识别。

### 4. 摄入与检索

> 因为本包不再被 `pip install -e` 安装，`rag` 命令可能不存在——
> 请在本目录下用 `python -m wealth_rag.cli`，或先设置
> `$env:PYTHONPATH = "$PWD\src"`。

```powershell
cd backend\RAG_

# 放入你的知识文档后摄入（支持 .pdf/.docx/.pptx/.xlsx/.md/.html）
python -m wealth_rag.cli ingest ./data --rebuild

# 查看知识库统计
python -m wealth_rag.cli info

# 自然语言检索
python -m wealth_rag.cli search "什么是再平衡策略？"
```

---

## CLI 命令

| 命令 | 说明 |
| --- | --- |
| `rag ingest [路径] [--rebuild]` | 摄入文档。默认增量（文档级覆盖）；`--rebuild` 清空索引全量重建 |
| `rag search "查询" [--top-k 8] [--no-rerank] [--doc <doc_id>]` | 混合检索。可关闭重排做对照，或限定单个文档 |
| `rag eval [--queries eval/queries.jsonl] [--top-k 10] [--no-rerank]` | 运行评估集，输出 Recall@k / MRR / nDCG@k |
| `rag info` | 知识库统计（设备、模型、文档数、切块数、稀疏词表） |

所有命令均支持 `--config <路径>` 指定配置文件（默认 `./config.yaml`）。

### 作为 Python 库使用

```python
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever

settings = load_settings("config.yaml")
retriever = Retriever(settings)

for hit in retriever.search("个人养老金的缴费上限是多少？", top_k=5):
    print(hit.rerank_score, hit.citation)   # citation = 文档 | 页码 | 标题路径
    print(hit.raw_text)                     # 证据正文
    # hit.parent_text                       # 所属「节」全文，供上下文扩展
```

---

## 配置说明（`config.yaml`）

关键参数（全部可调，修改后重新摄入生效）：

```yaml
device: auto                    # auto → 有 CUDA 用 GPU，否则 CPU

chunking:
  max_tokens: 512               # 子块上限（tiktoken cl100k_base）；实体保护可放宽至 640
  delimiters: "\n!?。；！？"     # 切分分隔符（含中文标点）
  heading_max_level: 7          # 允许的最大**语义层级**（1=章 … 7=第X条）
  overlap_tokens: 0             # 块间重叠 token 数（0 = 不重叠）
  breadcrumb: true              # 面包屑注入
  include_doc_title: true       # 面包屑首层是否带文档标题
  running_header_min_count: 3   # 标题重复 ≥N 次且近似 doc_title → 判为页眉并丢弃
  drop_contents: true           # 删除目录区块
  title_rules: true             # 中文标题语义层级识别 + 伪标题过滤
  min_chunk_chars: 30           # 小于该长度的块并入同节相邻块
  merge_documents: true         # 识别合订本文书边界并重置层级栈

retrieval:
  dense_top: 30                 # 稠密召回数
  sparse_top: 30                # 稀疏召回数
  fused_top: 20                 # RRF 融合后进入重排的候选数
  final_top: 8                  # 最终返回数
  no_answer_threshold: 0.05     # 重排最高分低于此值 → 判定库中无相关证据

sparse:
  method: qdrant_sparse         # qdrant_sparse（默认）| jieba_bm25（兜底）

parsing:
  pdf_engine: auto              # auto（Docling 优先，失败自动回退 PyMuPDF）| docling | pymupdf
  garbled_chars: "摇郾也冶页盂藔"  # PDF CID 映射错误字符，替换为空格以恢复词边界
```

---

## 核心设计：六步切分策略

切分质量是检索质量的上限。本模块以 **RAGFlow 的具体实现为模仿蓝本**，逐项适配：

| 步骤 | 做法 | 模仿来源 |
| --- | --- | --- |
| ① 结构切分 | 以 Markdown 标题树为骨架，按最小完整「节」切分；层级以**标题文本自身的编号体系**为准（第X章 > 第X节 > 一、 > （一）），Markdown 的 `#` 数量仅作兜底 | `rag/flow/chunker/title_chunker/`、`rag/nlp/__init__.py::BULLET_PATTERN` |
| ② 节内切分 | split-then-merge：按中文标点切分 → tiktoken 计数 → 贪心合并至 512 tokens | `rag/app/naive.py::naive_merge()` |
| ③ 超限兜底 | 超长单元先按句子边界拆分（英文句号后接空白触发，`4.5%`/`U.S.` 不受影响），必要时硬切分 | TitleChunker hard split |
| ④ 表格处理 | 表格为原子块；超限按行切分且**重复表头**；`chunk_type` 标记 `table` / `table_continued` | `rag/app/table.py` |
| ⑤ 父子块组装 | `mom`（节全文，**独立点存储**，无向量）/ `text`（子块，参与检索）两级结构 | TokenChunker `mom`/`text` |
| ⑥ 元数据组装 | 面包屑注入 + 页码溯源 + **金融实体保护**（不切断数字/金额/条款编号） | 我方适配与新增 |
| ⑦ 噪声治理 | 页眉识别、目录剔除、伪标题降级、纯噪声块丢弃、过短块同节合并 | `rag/nlp/__init__.py::not_title / remove_contents_table` |

其中「面包屑注入」是 late chunking 的低成本替代：每个子块文本前置标题路径（如 `资产配置 > 再平衡策略`），
使嵌入与关键词匹配都自带上下文，无需为嵌入付出 2–3 倍开销。

> **标题判定集中在 `ingestion/heading_rules.py`**，包含三条关键工程判断：
> ① **层级读标题文本，不读 `#` 数量**——实测同一份代码走 Docling 得到「全部 H2」、走 PyMuPDF
> 得到「H3–H6 混合」，两条通道都拿不到正确层级，而编号体系（第X章/第X节/一、）始终可靠；
> ② **页眉/目录属于导航元数据，识别后整行丢弃**，不降级为正文；
> ③ **编号重启 = 上级作用域已更换**——解析器丢失上级标题时（本项目 PDF 实际丢失了第 2–5 章），
> 若不处理，后续所有「第X节」都会被错误挂到「第一章」下（实测污染 38% 的块，处理后降到 7%）。

---

## 项目结构

```
backend/RAG_/                   # ← 本模块（Agent 的子模块，非独立项目）
├── config.yaml                 # 全局配置（切分 / 检索 / 模型 / 设备）
├── data/                       # 知识文档目录（samples/ 为内置测试样例）
├── db/                         # Qdrant 本地库 + 稀疏词表（运行时生成）
├── models/                     # 本地模型（由下载脚本生成）
├── scripts/download_models.ps1 # ModelScope 模型下载脚本
├── docs/audit/                 # 只读诊断脚本（切分产物 / 面包屑 / 检索实测）
├── eval/                       # 评估集、评估脚本、阈值校准工具链
│   ├── queries.jsonl           #   70 条 chunk 级评估集（63 可答 + 7 负例）
│   ├── queries.calibration.jsonl  #  补充负例
│   ├── build_eval_pool.py      #   候选池与标注模板生成
│   ├── apply_grades.py         #   标注合并与覆盖率校验
│   └── tune_rerank_threshold.py   # 阈值校准（含 rerank 开/关 A/B）
└── src/wealth_rag/             # Python 包
    ├── cli.py                  # CLI 入口（ingest / search / eval / calibrate / info）
    ├── config.py  models.py    # 配置加载 / 数据模型（Chunk、SearchResult）
    ├── evaluation.py           # chunk 级评估（bootstrap CI / 配对检验 / 评答指标）
    ├── thresholds.py           # 阈值判定纯逻辑（无第三方依赖，可独立测试）
    ├── calibration.py          # 阈值校准实现
    ├── ingestion/              # parser · chunker · heading_rules · outline · tokenizer · pipeline
    ├── embedding/              # Qwen3-Embedding 封装（设备自适应）
    ├── sparse/                 # jieba 分词 + BM25 / 稀疏向量
    ├── store/                  # Qdrant 本地库封装
    └── retrieval/              # hybrid（双路召回 + RRF）· rerank（Qwen3-Reranker）
```

> **已移除的独立项目遗留**（2026-10-15，见 `问题文档/问题与解决方案.md` P2-25 ~ P2-27）：
> `pyproject.toml`（依赖已并入根 `requirements.txt`）、`src/wealth_rag.egg-info/`
> （`pip install -e` 产物）、`.idea/`（IDE 工程配置）、`patch/`（已失效的补丁机制，
> 其记录已合并进问题文档）、`eval/queries.example.jsonl`（已被真实评估集取代）。

---

## 已验证结果

开发环境实测（RTX 4060 Laptop 8GB / Python 3.12 / torch 2.9.1+cu128）：

| 环节 | 结果 |
| --- | --- |
| 切分 | 长节自动生成父子块、表格独立成块并文本化、面包屑注入、页眉/目录剔除；子块上限 512 token，**实体保护可放宽至 640**（`_merge_segments` 的 `pull_limit = 1.25×`） |
| 解析 | md/docx/xlsx/html → Docling 输出带标题层级 Markdown；PDF → PyMuPDF 通道（含页码） |
| 存储 | Qdrant 本地模式原生支持稀疏 + IDF 修正 + RRF（单请求完成双路融合） |
| 编码 | Qwen3-Embedding 本地 GPU，1024 维归一化向量，语义排序正确 |
| 重排 | 相关块 0.79 / 0.89 vs 无关块 0.004，区分度明确 |
| 端到端 | 5 篇样例文档、14 块，全量重建摄入 25.9s；热查询秒级响应 |
| 评估 | 6 条样例查询 Recall@10 / MRR / nDCG@10 = 1.000。⚠️ 该指标为**文档级**（`evaluation.py` 只匹配 doc_id/doc_title/source_path），对切分质量不敏感，**不能作为切分优劣的依据**；需另建 chunk 级评估集 |
| 英文 | 结构切分与表格正常；超长段落句子边界兜底（0/2 句中切断）；英文查询命中 0.72，中文查询跨语言命中同一英文块 0.70 |

---

## 文档索引

| 文档 | 内容 |
| --- | --- |
| [PLAN.md](./PLAN.md) | 设计与实施计划书：需求边界、技术选型调研、里程碑 |
| [TECH_SUMMARY.md](./TECH_SUMMARY.md) | 技术总结：整体架构、12 个技术要点的候选方案对比与选用理由、实测结果与踩坑记录 |
| [DEPLOY.md](./DEPLOY.md) | 部署指南：目标机环境安装、模型下载、验证清单、已知问题对策 |

---

## 已知限制与后续演进

1. **编排已接入，见 Agent**：本模块仍只负责检索；「什么时候检索、检索到之后怎么答」
   由 `backend/core/agent/` 与 `backend/core/tools/tools_hub/rag_tools.py` 负责。
   融合的触发策略与输出契约见 `Investigation/`；
2. **阈值尚未实测校准**：`config.yaml` 的 `no_answer_threshold` / `rerank_floor`
   仍是经验值。校准工具链已就绪（`eval/tune_rerank_threshold.py`），
   但需要先做人工标注（`eval/build_eval_pool.py` → `apply_grades.py`）；
   现有基线显示当前值**漏拒率偏高**（库内无答案时仍会作答）；
3. **语义切分 / late chunking 未启用**：当前成本收益比不成立，待真实评估集验证后决定；
4. **多模态未覆盖**：研报图表、扫描件解析不在本阶段；
5. **评估集已就位**：`eval/queries.jsonl` 为 70 条 chunk 级评估集（63 可答 + 7 负例），
   指标与统计方法见 [`docs/评估框架与指标设计.md`](./docs/评估框架与指标设计.md)；
   不足是**分级标注（0/1/2）仍缺**，故 `rerank_floor` 无法自动校准；
6. **显存约束**：当前锁定 0.6B 模型；更大模型（4B/8B）可评估量化方案后启用。
