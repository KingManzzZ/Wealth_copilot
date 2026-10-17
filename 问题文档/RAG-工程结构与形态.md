# RAG 工程结构与形态 · 问题与解决方案

> **模块定位**：项目形态、依赖声明、导入路径、开发环境。覆盖仓库根 `requirements.txt`、
> `backend/RAG_/` 的目录形态、以及"取消独立项目状态"的清理工作。
>
> **本文件是拆分自 [`问题与解决方案.md`](./问题与解决方案.md) 的模块文档**，
> 索引与跨模块经验见 [`README.md`](./README.md)。
>
> **为什么单独成文**：这一批问题**没有一个是"代码写错了"**——代码逻辑全对，
> 毛病出在"代码所假设的项目结构已经变了，而结构没人去改"。
> 它们不会让任何测试失败，也不会在开发机上暴露，因此最容易被长期忽略。

## 问题索引

| 编号 | 问题 | 严重度 | 状态 | 子模块 |
| :--- | :--- | :--- | :--- | :--- |
| P0-9 | 开发环境无法执行任何 shell 命令 | 🔴 阻塞 | ❌ 未解决（本会话 shell 仍被沙箱 ACL 阻塞；需切「完全权限」或修复该目录的 Windows 文件权限） | 环境 |
| P2-8 | CLI 默认相对路径在仓库根目录下失效 | 🔵 低 | ✅ 已修复（已加 `_existing_or_project_path`，CWD 优先 + 模块根兜底） | 导入路径 |
| P2-9 | 文档记录的诊断脚本工作目录已过期 | 🔵 低 | ✅ 已修复（问题文档路径已更正；但 `docs/audit/` 有 4 个脚本仍用相对 `src`，见下方备注） | 文档 |
| P2-25 | 依赖声明分裂：两个清单互不知晓 | 🟠 中高 | ✅ 已修复（依赖已并入根 `requirements.txt`，RAG 的 `pyproject.toml` 已清空） | 依赖 |
| P2-26 | `import wealth_rag` 依赖一次早已失效的安装 | 🟠 中高 | ✅ 已修复（代码，未实测——`_rag_impl._ensure_wealth_rag_importable` 已显式补 `sys.path`） | 导入路径 |
| P2-27 | `patch/` 机制已失效，但记录与快照仍在 | 🔵 低 | ✅ 已修复（记录已合并进问题文档；`RAG_/patch/` 目录待人工删除） | 清理 |

---

### P0-9 · 开发环境无法执行任何 shell 命令

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 执行 `python --version` 等命令时，在沙箱纳管阶段即报错 |
| **严重度** | 🔴 阻塞（无法运行测试、无法实测、无法验证任何改动） |
| **状态** | ❌ 未解决（本会话 shell 仍被沙箱 ACL 阻塞；需切「完全权限」或修复该目录的 Windows 文件权限） |
| **所属环节** | 开发环境 |

#### 🔍 现象

本阶段**没有任何一条 shell 命令成功执行过**——包括 `python --version`、`Get-ChildItem`、
`Remove-Item`，甚至**只读命令**。全部在授权阶段失败：

```
Error: SetNamedSecurityInfoW failed (Win32 5): grantWrite(E:\Python_Projects\Agent_study)
```

#### 🧪 证据

| 命令 | 预期 | 实际 |
| :--- | :--- | :--- |
| `python --version` | 输出 Python 版本 | ❌ 同样的 ACL 报错 |
| `Get-ChildItem E:\Python_Projects\Agent_study` | 列出目录 | ❌ 同样的 ACL 报错 |
| `Remove-Item <临时文件>` | 删除文件 | ❌ 同样的 ACL 报错 |

注意报错发生在 **`grantWrite(E:\Python_Projects\Agent_study)`** —— 即沙箱试图给
**工作区根目录**加写权限时失败，与具体命令无关。因此**不是命令被拒，而是沙箱无法纳管这个工作区**。

#### 🎯 根因

DSH 的文件沙箱在启动时需要对工作区根目录设置访问控制项（为当前用户授予写权限）。
该操作返回 Win32 错误 5（`ERROR_ACCESS_DENIED`）。由于工作区纳管失败，
后续所有需要经过沙箱的操作（包括文件系统只读访问）一并失败。

这与本文档开头「修复状态」一节记录的现象是**同一类问题的更严重形态**：

> 修复代码以补丁形式交付……（当前会话的文件沙箱不允许直接改写项目既有文件）

第一次只影响**写**（于是有了 `patch/` 目录这一变通方案）；本次连**读和执行**都不可用。

#### ✅ 解决方案

**已采用的变通（本次实际执行）**：

1. **改用文件工具完成全部调研**：`glob` / `read` / `grep` / `write` 走的是另一条文件后端，
   **不受沙箱影响**。靠这条路径完成了：读取 RAG 模块全部在线代码、读取 Agent 全部工具层代码、
   写出 5 份调研文档与 6 个新增代码文件。
2. **不编造执行结果**：所有未经实测的结论一律标注「未实测」。例如阈值校准工具链写完后，
   明确说明「一行都没执行过」，并要求使用者先跑 `selftest_calibration.py` 验证。

**未解决的阻塞**（需要人工介入）：

| 待办 | 为什么阻塞 |
| :--- | :--- |
| 跑 `python eval/selftest_calibration.py` | 验证新增校准逻辑的数学正确性 |
| 跑 `python eval/tune_rerank_threshold.py` | **阈值校准的实测本身**，本阶段的核心交付依赖它 |
| `git diff` / `git commit` | 无法提交本阶段产出 |

**根治方式**：把本会话切到「完全权限」（full access），或修复该目录的 Windows 文件权限。
若选择后者，DSH 附带了一个一次性诊断修复脚本，它会在同一轮里读取该路径及其所有上级目录、
为当前用户补上缺失的权限、并打印每项改动的回滚命令。

#### ✔️ 验证

无法在本环境验证。**使用者需自行执行**：

```powershell
cd E:\Python_Projects\Agent_study\backend\RAG_
python eval\selftest_calibration.py       # 期望：✅ 全部通过
```

#### 💡 学习要点

1. **工具链故障要第一时间确认边界，而不是绕路。** 早期只发现「写文件失败」，仍尝试用命令做只读检查；
   确认「连只读命令都失败」后才停止尝试，改为纯文件工具推进——**早一步确认边界能省下多次无效重试**。
2. **在无法验证的环境里，交付物必须自带验证入口。** 于是有了 `selftest_calibration.py`：
   不加载模型、不需要知识库、几秒钟出结果，专门让使用者一条命令就能验证逻辑正确性。
3. **能力缺失要在文档里显式声明，不能沉默。** 本阶段所有「未实测」结论都做了标注——
   否则使用者会误以为这些数字已经验证过，那是比没有数字更危险的状态（对应第一阶段的元教训）。

---

### P2-8 · CLI 默认相对路径在仓库根目录下失效

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 实现 `rag calibrate` 时推演使用者会从哪里调用 |
| **严重度** | 🔵 低 |
| **状态** | ✅ 已修复（已加 `_existing_or_project_path`，CWD 优先 + 模块根兜底） |
| **所属环节** | 集成 |

#### 🔍 现象

`config.yaml` 与 `eval/` 都位于**模块目录** `backend/RAG_/` 下，
但**独立项目形态下**包被 `pip install -e ".[dev,fast]"` 安装后，`rag` 命令**可以在任何目录调用**。
（2026-10-15 收编后该安装方式已取消，见 P2-25 / P2-26；`rag` 命令需改用
`python -m wealth_rag.cli`。）

此时 CLI 的默认值 `--queries eval/queries.graded.jsonl` 会按**当前工作目录**解析，
在仓库根目录下形同虚设：

```
E:\Python_Projects\Agent_study> rag calibrate
→ FileNotFoundError: eval/queries.graded.jsonl
```

#### 🧪 证据

`load_settings` 的既有行为也是按 CWD 解析（`config.py:137-139`）：

```python
if config_path is None:
    config_path = os.environ.get("WEALTH_RAG_CONFIG", DEFAULT_CONFIG_NAME)
path = Path(config_path).expanduser()
if not path.is_absolute():
    path = (Path.cwd() / path).resolve()
```

这是**既有设计**，但新增的 `calibrate` 命令让问题更明显——因为它依赖两个相对路径
（`--queries` 与 `--config`）同时正确。

#### 🎯 根因

**"包可在任意目录调用"与"资源相对 CWD 解析"这两个设计目標相互冲突。**

包内的资源（默认配置、评估集）天然相对**包/模块位置**，
而用户提供的路径天然相对**CWD**。把两者用同一套解析逻辑处理，必然有一方出错。

#### ✅ 解决方案

新增路径解析助手，**先试 CWD，再回退模块根**：

```python
def _existing_or_project_path(rel: str) -> str:
    """把相对路径解析成存在的那个：先按 CWD，再按项目根。

    包被 pip install -e 安装后，用户常从仓库根目录调用 rag_tools calibrate，
    （注：该安装方式已于 2026-10-15 取消，但本函数的作用仍然成立——
      用户可能从仓库根或模块目录两处调用）
    而 eval/ 与 config.yaml 都在模块目录下。按 CWD 找不到时回退到模块根，
    避免默认值在仓库根目录下形同虚设。
    """
    p = Path(rel).expanduser()
    if p.is_absolute() or p.exists():
        return str(p)
    module_root = Path(__file__).resolve().parents[2]   # src/wealth_rag/cli.py → 项目根
    candidate = module_root / rel
    return str(candidate) if candidate.exists() else str(p)
```

**注意回退顺序**：必须是「CWD 优先，模块根兜底」——
若反过来（模块根优先），用户显式传入的相对路径会被模块内的同名文件覆盖，
那是更严重的"静默用错文件"问题。

#### ✔️ 验证

```powershell
# 从仓库根目录调用，应能找到默认评估集
cd E:\Python_Projects\Agent_study
rag calibrate --help          # 不应报路径错误
```

#### 💡 学习要点

1. **区分"包内资源"与"用户输入"的路径语义。** 前者相对模块位置，后者相对 CWD。
2. **兜底顺序决定失败模式。** 「CWD 优先 + 模块根兜底」在两边都找不到时才失败；
   反过来则会静默用错文件——**后者比报错更糟**。
3. **新增命令时要重新检查既有的路径约定是否仍然合理。** 既有代码按 CWD 解析没问题，
   但新命令引入的默认值依赖改变了这个前提。

---

### P2-9 · 文档记录的诊断脚本工作目录已过期

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 阅读本文档「附：诊断脚本」一节，与实际目录结构对照 |
| **严重度** | 🔵 低 |
| **状态** | ✅ 已修复（问题文档路径已更正；但 `docs/audit/` 有 4 个脚本仍用相对 `src`，见下方备注） |
| **所属环节** | 文档 |

#### 🔍 现象

本文档末尾「附：诊断脚本」记录的工作目录是：

```powershell
cd E:\Python_Projects\RAG_
```

而实际项目位于：

```
E:\Python_Projects\Agent_study\backend\RAG_\
```

#### 🧪 证据

第一阶段（切分与检索问题）是在 `E:\Python_Projects\RAG_` 独立开发时完成的；
本阶段把该模块**移入**了 `Agent_study\backend\RAG_` 以融入 Agent 项目。
**文档里的路径没有随迁移更新。**

#### 🎯 根因

**模块迁移时只更新了代码，没有更新文档中的绝对路径。**

绝对路径写进文档的固有风险：任何目录调整都会让它失效，而且**失效是静默的**
（读者复制命令后得到"路径不存在"，需要自己推断是迁移导致）。

#### ✅ 解决方案

1. **已修正本文档末尾的命令**为 `cd E:\Python_Projects\Agent_study\backend\RAG_`。
2. **建议改用相对路径表述**，把"从哪个目录执行"绑定到仓库结构而不是绝对磁盘位置：

```powershell
cd <repo>\backend\RAG_      # 即包含 config.yaml 与 src/ 的目录
```

3. **迁移检查清单**：模块迁移后需搜索并更新：README / 部署文档 / 问题文档 / 脚本里的绝对路径。

#### ✔️ 验证

```powershell
cd E:\Python_Projects\Agent_study\backend\RAG_
python docs\audit\test_heading_rules.py     # 应能正常执行
```

#### 💡 学习要点

1. **绝对路径是文档腐化的高发点。** 能写相对路径就写相对路径，并说明"相对什么"。
2. **迁移/重构的变更清单要包含文档与脚本内的路径。** 与 P0-10 同源——
   都是"改了代码没改文档"，本条改的是路径，P0-10 改的是能力描述。
3. **静默失效的引用比失效的代码更麻烦**：代码会报错，文档不会。

#### 📌 后续发现（2026-10-15，拆分文档时核查）

本次核查发现同一问题在 `docs/audit/` 下**仍未修完**：4 个脚本用的是
`sys.path.insert(0, "src")`（相对**当前工作目录**），另 3 个用
`Path(__file__).resolve().parents[2] / "src"`（相对**脚本位置**）。两种写法混用，
因此前 4 个脚本只有在 `backend/RAG_/` 下执行才可用——从仓库根执行会失败。

| 脚本 | `sys.path` 写法 | 是否依赖 cwd |
| :--- | :--- | :--- |
| `dump_chunks.py` | `"src"` | ⚠️ 是 |
| `diagnose_breadcrumb.py` | `"src"` | ⚠️ 是 |
| `search_probe.py` | `"src"` | ⚠️ 是 |
| `test_chunker_rules.py` | `"src"` | ⚠️ 是 |
| `verify_fix.py` | `Path(__file__).parents[2]/"src"` | ✅ 否 |
| `test_outline.py` | 同上 | ✅ 否 |
| `test_heading_rules.py` | 同上 | ✅ 否 |

**待办**：把前 4 个统一改为按脚本位置定位（`Path(__file__).resolve().parents[2] / "src"`）。
这与 P2-8 是同一类问题的复现——修了 `cli.py`，但 `docs/audit/` 没一起修。

---

### P2-25 · 依赖声明分裂：两个清单互不知晓

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 为回答"如何取消 RAG 的独立项目状态"而清点工程结构时发现 |
| **严重度** | 🟠 中高（会导致线上静默降级） |
| **状态** | ✅ 已修复（依赖已并入根 `requirements.txt`，RAG 的 `pyproject.toml` 已清空） |
| **所属环节** | 工程结构 / 依赖管理 |
| **代码位置** | `requirements.txt`（根）、`backend/RAG_/pyproject.toml` |

#### 🔍 现象

依赖被声明在两个互不知晓的地方：

| 声明位置 | 内容 |
| :--- | :--- |
| 根 `requirements.txt` | `openai` / `python-dotenv` / `fastapi` / `uvicorn[standard]` / `sqlalchemy` |
| `backend/RAG_/pyproject.toml` | `docling` / `qdrant-client` / `transformers` / `torch` / `tiktoken` / `jieba` / `rank-bm25` / `numpy` / `typer` / `rich` / `pydantic` / `pyyaml` |

**根清单里一个 RAG 依赖都没有。**

#### 🧪 证据

后果链条（这是本条被评为"高"的原因）：

```
新环境按主项目方式装依赖：pip install -r requirements.txt
        ↓  没装 torch / transformers / qdrant-client
python main.py 正常启动（Agent 不 import 这两个包）
        ↓
用户提问政策问题 → 模型调用 search_knowledge
        ↓
_rag_impl._build() → from wealth_rag... → ImportError
        ↓
异常被翻译成 {"error": ...}（这是设计上正确的降级）
        ↓
但服务端只打一行 "[rag] 预热失败…"，前端与运维都看不到
        ↓
表现：Agent 能聊天，但永远查不到资料，且没有任何显式症状
```

**这正是 P2-15 里我自己引入的可观测性缺口**：为了让"可选能力故障不拖垮服务"而吞掉异常，
代价是故障变成静默的。

#### 🎯 根因

**"独立项目"的形态让依赖归属失去了唯一性。**

RAG 有自己的 `pyproject.toml`，所以它"自带"依赖声明；父项目有自己的 `requirements.txt`，
所以它只管自己。融合之后两边都"认为对方会装"——**没有任何机制会检查这件事**。

更深一层：**这是"形态遗留"而非"代码缺陷"**。代码逻辑全对，但工程结构还在按
"两个项目"的假设运作。

#### ✅ 解决方案

**(a) 依赖收敛到根 `requirements.txt`，并按用途分组**：

```text
# ---------- Agent 后端（backend/）----------
openai / python-dotenv / fastapi / uvicorn[standard] / sqlalchemy

# ---------- RAG 检索模块（backend/RAG_/）----------
transformers / torch / qdrant-client / tiktoken / jieba / rank-bm25
numpy / pydantic / pyyaml / typer / rich

# ---------- RAG 文档摄入（仅摄入时需要，检索不需要）----------
docling

# ---------- 可选：PDF 快速通道 + Office 格式 ----------
pymupdf / pymupdf4llm / python-docx / openpyxl / modelscope
```

并写明 **GPU 版 torch 必须单独装**（从 PyPI 默认装到的是 CPU 版，
检索能跑但重排会慢 10 倍以上）——这是一条极易踩的坑，见 P1-1 的同类记录。

**(b) 删除 `backend/RAG_/pyproject.toml`**，让"依赖声明"只有一处。

#### ✔️ 验证

```powershell
# 全新环境应能一次性装齐
python -m pip install -r requirements.txt
python -c "import torch, transformers, qdrant_client, jieba, tiktoken; print('RAG deps OK')"
```

#### 💡 学习要点

1. **"独立项目"的形态会分散依赖声明，而融合后没有任何机制检查它们是否齐备。**
   合并项目的第一件事应该是合并依赖清单，不是改代码。
2. **可选能力的降级越优雅，越需要显式的健康检查。** 吞异常是"不让它拖垮服务"，
   但必须配一个"能看出它坏了"的通道，否则优雅降级 = 静默失效。
3. **CPU 版 torch 是这类项目的经典暗坑。** 从 PyPI 装 `torch` 不会报错，
   只是慢——没有断言会失败，只有用户体验变差。

---

### P2-26 · `import wealth_rag` 依赖一次早已失效的安装

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 追查"删掉 pyproject.toml 会不会导致 import 失败"时全库清点 |
| **严重度** | 🟠 中高 |
| **状态** | ✅ 已修复（代码，未实测——`_rag_impl._ensure_wealth_rag_importable` 已显式补 `sys.path`） |
| **所属环节** | 工程结构 / 导入路径 |
| **代码位置** | `backend/core/tools/tools_hub/_rag_impl.py`、`src/wealth_rag.egg-info/` |

#### 🔍 现象

`_rag_impl.py` 的 `_build()` 里直接写：

```python
from wealth_rag.config import load_settings
from wealth_rag.retrieval.hybrid import Retriever
```

而 `wealth_rag` 位于 `backend/RAG_/src/wealth_rag`——**不在 `sys.path` 上**。
它能 import 成功，完全依赖"这个包曾经被 `pip install -e` 装过"。

`backend/RAG_/src/wealth_rag.egg-info/` 正是那次安装的产物（含 `top_level.txt`、
`entry_points.txt` 里的 `rag = wealth_rag.cli:main`）。

#### 🧪 证据

全库清点"谁自己处理了导入路径"：

| 文件 | 是否自己插 `sys.path` |
| :--- | :--- |
| `docs/audit/dump_chunks.py` 等 7 个诊断脚本 | ✅ 都插了 `src` |
| `eval/validate_queries.py` / `selftest_calibration.py` | ✅ 都插了 |
| **`backend/core/tools/tools_hub/_rag_impl.py`** | ❌ **没插** ← 唯一的依赖安装的消费者 |

也就是说：**项目内所有自带脚本都遵循"自己插 `src`"的约定，只有我新写的集成代码没有。**
这让集成层成了一个隐式依赖"外部安装状态"的特例。

#### 🎯 根因

**我在写集成代码时，实际上假设了"RAG 是装好的包"**——这个假设在开发机上成立
（`.egg-info` 就是证据），但它**不可迁移**：换环境、换机器、CI 里都会失效，
而且失效方式是"首次检索才报错"，不是启动时报错。

#### ✅ 解决方案

**(a) 在集成层显式补路径，让后端自包含**：

```python
def _ensure_wealth_rag_importable() -> None:
    """把 backend/RAG_/src 加进 sys.path，让 import wealth_rag 可用。

    RAG 原本靠 pyproject.toml + pip install -e 提供 wealth_rag；收编后
    pyproject.toml 已删除，因此不能再假设这个包被安装过。
    （RAG 目录下的 docs/audit/*.py 与 eval/*.py 本来就用同样手法自己插 src，
     所以这是项目内既有约定，不是特例。）
    """
    src = DEFAULT_RAG_ROOT / "src"
    if not src.is_dir():
        return
    p = str(src)
    if p not in sys.path:
        sys.path.insert(0, p)
```

在 `_build()` 里 import 之前调用（幂等）。

**(b) 明确取舍**：若希望完全不碰 `sys.path`，替代方案是在根 `requirements.txt` 里写
`-e ./backend/RAG_` 并保留 `pyproject.toml`——**但那等于把这个目录重新变成可安装项目，
与"取消独立项目状态"的目标相反**。故选择显式补路径。

**(c) 删除 `src/wealth_rag.egg-info/`**（安装产物，重建环境时自动生成）。

#### ✔️ 验证

```powershell
# 在"没有装过 wealth-rag"的环境里应能成功
python -c "from core.tools.tools_hub import _rag_impl as r; print(r.ensure_importable()); import wealth_rag; print('OK')"
```

#### 💡 学习要点

1. **"能 import"不等于"路径配好了"。** 开发机上因历史安装而可用，是最隐蔽的
   "在我机器上能跑"。
2. **看项目内的既有约定。** 7 个脚本都自己插 `src`，说明约定早已存在；
   我的代码没follow，是集成时没有对齐约定，而不是项目缺了机制。
3. **隐式依赖安装状态的代码，失效点总在最后一步。** 它不会在启动时报错，
   只会在"首次真正用到"时报错——那时排查成本最高。

---

### P2-27 · `patch/` 机制已失效，但记录与快照仍在

| 元信息 | 内容 |
| :--- | :--- |
| **发现时间** | 2026-10-15 |
| **发现方式** | 清点 RAG 目录冗余时，发现 `patch/` 里只剩 `PATCH_NOTES.md` 与一份备份 |
| **严重度** | 🔵 低 |
| **状态** | ✅ 已修复（记录已合并进问题文档；`RAG_/patch/` 目录待人工删除） |
| **所属环节** | 文档 / 清理 |
| **代码位置** | `backend/RAG_/patch/` |

#### 🔍 现象

`RAG_/patch/` 下只有两个东西：

- `PATCH_NOTES.md`（101 行变更记录，状态标注"已应用"）
- `_backup_20261015_201639/`（11 个文件的改动前快照）

而 `PATCH_NOTES.md` 里反复引用的 `apply_fix.ps1` **根本不存在**。

#### 🧪 证据

```
backend/RAG_/patch/PATCH_NOTES.md
backend/RAG_/patch/_backup_20261015_201639/{README.md, config.yaml, src/wealth_rag/*.py}
# ↑ 没有 apply_fix.ps1
```

`PATCH_NOTES.md` 自己也写明"补丁已通过单次提权执行 `apply_fix.ps1` 应用完毕，
现只保留变更记录与回滚快照"。也就是说：**补丁机制已完成使命并自我失效**，
留下的是一份历史记录 + 一份只在"要回滚到改动前"时才有用的快照。

#### 🎯 根因

**临时变通方案的残留没有被清理。** `patch/` 是为绕开当时沙箱限制而生的权宜之计
（见 P0-9：沙箱连写既有文件都不允许）。限制解除、补丁应用之后，
这套机制失去了存在理由，但目录被留下了。

#### ✅ 解决方案

**(a) 把记录合并进本文档**（唯一事实来源）。本节前面的「修复状态」已改为承载
`PATCH_NOTES.md` 的全部内容——包括逐项对照表、验收结果、未处理限制、
第二批修复、以及回滚步骤。合并的理由：

- 本文档已经是问题与修复的权威记录，再留一份 `PATCH_NOTES.md` 就是重复；
- 两份记录会各自过时（P2-4 / P0-10 已经吃过一次"文档与实现不符"的亏）。

**(b) 删除 `RAG_/patch/` 整个目录。**

**代价（必须说清）**：**文件级回滚能力随之取消**。改动前的 11 个文件快照不再保留。
若需要回退，依赖 git 历史；改动前的**量化基线**仍完整保存在本文档的
「基线指标」与「前后实测对比」两节，可用于判断是否回退。

#### ✔️ 验证

```powershell
Test-Path backend\RAG_\patch      # 期望 False
```

#### 💡 学习要点

1. **权宜之计要有明确的退役动作。** 临时方案最容易变成永久遗留，因为它"不碍事"。
2. **同一事实不要有两份记录。** 两份必然分叉，而分叉的文档比没有文档更危险
   （这正是 P0-10 的教训：错误的权威断言会让人做错事）。
3. **删掉回滚能力是一个需要显式说明的代价。** 清理时要区分"删冗余"与"删退路"，
   后者必须写清替代方案（这里是 git + 保留的量化基线）。
