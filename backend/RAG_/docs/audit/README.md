# 审查诊断脚本（audit）

配合 [`../../CHUNKING_REVIEW.md`](../../CHUNKING_REVIEW.md) 使用的只读诊断工具，
用于在改动切分/检索策略前后**量化对比**。

## 用法

在**项目根目录**执行（脚本依赖 `config.yaml` 与 `src/` 的相对路径）：

```powershell
# 默认读取 ./config.yaml；也可用环境变量指向其它配置
$env:WEALTH_RAG_CONFIG = ".\config.yaml"
$env:PYTHONIOENCODING = "utf-8"      # Windows 下保证中文输出不乱码

python docs\audit\dump_chunks.py            # 导出全部块的形态（含标题覆盖率、块类型分布）
python docs\audit\diagnose_breadcrumb.py    # 面包屑质量诊断（深度分布、高频标题、页眉污染）
python docs\audit\search_probe.py           # 真实检索实测（含同节重复检测）
python docs\audit\test_chunker_rules.py     # 切分规则单测（标题层级 / overlap / PDF 标题层级）
python docs\audit\test_heading_rules.py     # heading_rules 回归测试（纯规则，不加载模型，最快）
python docs\audit\test_outline.py           # 章级大纲重建回归（12 章断言，不加载模型）
python docs\audit\verify_fix.py             # 验收：区分父块/子块的完整指标 + 章级覆盖率
```

## 各脚本看什么

| 脚本 | 关键输出 | 对应报告章节 |
| --- | --- | --- |
| `dump_chunks.py` | 总块数、每文档块数、`chunk_type` 分布、`raw_text` 是否含标题、正文长度分布、短块清单 | P0-2、P1-1 |
| `diagnose_breadcrumb.py` | `heading_path` 深度分布、高频重复标题（页眉）、面包屑零信息量块数 | P0-1、P0-3、P0-3b |
| `search_probe.py` | 每条查询的 top-8 明细、`raw_text` 是否含标题、同一 `heading_path` 重复命中 | P0-1、P0-2、P1-2 |
| `test_chunker_rules.py` | H5/H6 是否被吞、`overlap_tokens` 是否生效、PDF 产出的标题层级分布 | P0-3、P2-1、P2-3 |
| `test_heading_rules.py` | 语义层级 / 伪标题 / 页眉 / 目录 / 文书边界 / 噪声判定的断言测试 | P0-1、P0-2、P0-4、P0-5、P0-6、P1-2 |

## 验收基线（改动前实测，改动后应改善）

| 指标 | 改动前 | 目标 |
| --- | --- | --- |
| `heading_path` 深度为 1 的块占比 | 98.3% (710/722) | 显著下降 |
| `raw_text` 含自身标题的块占比 | **0.14%** (1/722) | > 95% |
| `heading_path` 等于书名的块数（页眉污染） | 57 | 0 |
| 目录页块数 | 2 | 0 |
| 正文 <40 字符的块数 | 30 | 接近 0 |

## 注意

- 这些脚本**只读数据**，但 Qdrant 本地模式会在 `db/qdrant/` 下以 `r+` 打开 `.lock` 文件，因此**需要对 `db/` 有写权限**。
  普通环境下直接可用。若运行在受限沙箱里（症状为 `PermissionError: ... db\qdrant\.lock`），
  可把 `db/qdrant` 复制到可写位置，并用自定义 `config.yaml` 指向副本：

  ```powershell
  # 1) 复制库到可写目录（示例）
  New-Item -ItemType Directory -Force -Path .\_audit_db | Out-Null
  Copy-Item -Recurse -Force .\db\qdrant .\_audit_db\qdrant
  Copy-Item -Force .\db\sparse_vocab.json .\_audit_db\sparse_vocab.json
  Remove-Item .\_audit_db\qdrant\.lock -Force -ErrorAction SilentlyContinue

  # 2) 造一份指向副本的配置（db_dir 相对于配置文件所在目录解析）
  #    把原 config.yaml 内容复制一份，仅改 paths.db_dir / paths.cache_dir
  $env:WEALTH_RAG_CONFIG = ".\_audit_db\config.yaml"
  ```

- `test_chunker_rules.py` 的测试 3 会自动选取 `data_dir` 下的第一个 PDF，并对前 30 页做版面/OCR 解析，
  **首次运行较慢**（需加载版面模型）；无 PDF 时会自动跳过。
- `search_probe.py` 会加载嵌入与重排模型，CPU 下较慢（GPU 会明显更快）。
