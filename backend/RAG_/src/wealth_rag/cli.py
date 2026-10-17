"""CLI 入口（Typer）。

    rag_tools ingest ./data [--rebuild]      摄入知识文档
    rag_tools search "查询" [--top-k 8] [--no-rerank] [--doc <doc_id>]
    rag_tools eval [--queries eval/queries.jsonl] [--top-k 10] [--no-rerank]
    rag_tools calibrate [--queries eval/queries.graded.jsonl] [--ab]   校准 rerank 阈值
    rag_tools info                           知识库统计
"""

from __future__ import annotations

from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .config import load_settings

app = typer.Typer(help="个人财富规划 RAG 检索模块", no_args_is_help=True)
console = Console()


def _load(config: str | None):
    return load_settings(config)


@app.command()
def ingest(
    path: str | None = typer.Argument(None, help="文档目录或文件（默认取 config.yaml 的 data_dir）"),
    rebuild: bool = typer.Option(False, "--rebuild", help="全量重建（清空索引并从零摄入）"),
    config: str | None = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """摄入知识文档（增量或重建）。"""
    from rich.progress import (
        BarColumn,
        MofNCompleteColumn,
        Progress,
        SpinnerColumn,
        TextColumn,
        TimeElapsedColumn,
    )

    from .ingestion.pipeline import ingest as run_ingest

    settings = _load(config)
    target = path or str(settings.paths.data_dir)
    console.print(f"[bold]开始摄入[/bold]：{target}（rebuild={rebuild}）")

    progress = Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        BarColumn(),
        MofNCompleteColumn(),
        TimeElapsedColumn(),
        console=console,
    )
    files_task = progress.add_task("[cyan]准备中", total=None)
    encode_task = progress.add_task("[magenta]向量编码", total=None)
    counter = {"done": 0}

    def on_event(event: str, **data) -> None:
        if event == "start":
            progress.update(
                files_task, total=data["files"] or None, completed=0, description="[cyan]解析切分"
            )
        elif event == "load":
            progress.update(files_task, description="[cyan]加载嵌入模型")
        elif event == "file_start":
            progress.update(files_task, description=f"[cyan]{data['path']}")
            progress.update(encode_task, total=None, completed=0)
        elif event == "encode":
            progress.update(encode_task, total=data["total"], completed=data["done"])

    def on_file(rep) -> None:
        counter["done"] += 1
        progress.update(files_task, completed=counter["done"])
        if rep.error:
            progress.console.print(f"  [red]✗ {rep.path}：{rep.error}[/red]")

    with progress:
        report = run_ingest(
            settings, target, rebuild=rebuild, on_file=on_file, on_event=on_event
        )
        progress.update(
            files_task, total=len(report.files), completed=len(report.files), description="[cyan]完成"
        )

    console.print(
        f"[bold]完成[/bold]：{report.ok_files}/{len(report.files)} 个文件，"
        f"共 {report.total_chunks} 块"
    )


@app.command()
def search(
    query: str = typer.Argument(..., help="中文自然语言查询"),
    top_k: int = typer.Option(8, "--top-k", help="返回证据块数量"),
    no_rerank: bool = typer.Option(False, "--no-rerank", help="关闭重排"),
    doc: str | None = typer.Option(None, "--doc", help="限定 doc_id"),
    config: str | None = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """混合检索（稠密 + 稀疏 + RRF + 重排）。"""
    from .retrieval.hybrid import Retriever

    settings = _load(config)
    retriever = Retriever(settings)
    results = retriever.search(query, top_k=top_k, use_rerank=not no_rerank, doc_id=doc)

    console.rule(f"检索：{query}")
    if not results:
        console.print("[yellow]未命中任何结果（知识库是否已摄入？）[/yellow]")
        return
    table = Table(show_lines=True)
    table.add_column("#", justify="right", width=3)
    table.add_column("得分", justify="right")
    table.add_column("来源", max_width=32, overflow="ellipsis")
    table.add_column("片段", max_width=60, overflow="ellipsis")
    for i, res in enumerate(results, start=1):
        score = f"{res.score:.4f}"
        snippet = " ".join(res.raw_text.split())
        tag = "" if res.chunk_type == "text" else f"[{res.chunk_type}] "
        parent = "[dim]（含父块）[/dim]" if res.parent_text else ""
        table.add_row(str(i), score, res.citation, tag + snippet[:120] + parent)
    console.print(table)


@app.command("eval")
def eval_cmd(
    queries: str = typer.Option("eval/queries.jsonl", "--queries", help="评估集 JSONL 路径"),
    top_k: int = typer.Option(10, "--top-k"),
    no_rerank: bool = typer.Option(False, "--no-rerank"),
    config: str | None = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """运行检索评估（Recall@k / MRR / nDCG@k）。"""
    from .evaluation import evaluate, render_report

    settings = _load(config)
    report = evaluate(settings, queries, top_k=top_k, use_rerank=not no_rerank)
    render_report(report)


def _existing_or_project_path(rel: str) -> str:
    """把相对路径解析成存在的那个：先按 CWD，再按模块根。

    使用者可能从**两处**调用：仓库根目录，或本模块目录（``backend/RAG_``）。
    而 ``eval/`` 与 ``config.yaml`` 都在模块目录下，所以按 CWD 找不到时回退到模块根，
    避免默认值在仓库根目录下形同虚设。

    > 注：本模块自 2026-10-15 起不再是可安装的独立项目（``pyproject.toml`` 已删除），
    > 因此不再假设 ``rag_tools`` 命令存在；但"从两处调用"这个前提依然成立。
    """
    p = Path(rel).expanduser()
    if p.is_absolute() or p.exists():
        return str(p)
    module_root = Path(__file__).resolve().parents[2]
    candidate = module_root / rel
    return str(candidate) if candidate.exists() else str(p)


@app.command()
def calibrate(
    queries: str = typer.Option(
        "eval/queries.graded.jsonl", "--queries", help="带 grades 分级标注的评估集"
    ),
    top_k: int = typer.Option(10, "--top-k"),
    min_cover: float = typer.Option(
        0.95, "--min-cover", help="positive 覆盖率下限（1−该值 = 最大可容忍误拒率）"
    ),
    floor_pct: float = typer.Option(0.25, "--floor-pct", help="rerank_floor 取 partial 的分位"),
    ab: bool = typer.Option(False, "--ab", help="额外跑 rerank 开/关的块级 A/B"),
    json_out: str = typer.Option(None, "--json", help="把校准结果落盘"),
    config: str = typer.Option(None, "--config", help="配置文件路径"),
) -> None:
    """用实测分数分布校准 no_answer_threshold / rerank_floor。

    需要带 ``grades`` 分级标注的评估集（由 ``eval/apply_grades.py`` 生成）。
    完整的标注流程见 ``eval/build_eval_pool.py`` 的模块文档。
    """
    from .calibration import calibrate as run_calibrate

    run_calibrate(
        _existing_or_project_path(queries),
        top_k=top_k,
        config=_existing_or_project_path(config) if config else None,
        min_cover=min_cover,
        floor_pct=floor_pct,
        do_ab=ab,
        json_out=json_out,
    )


@app.command()
def info(config: str = typer.Option(None, "--config", help="配置文件路径")) -> None:
    """知识库统计。"""
    from .store.qdrant_store import QdrantStore

    settings = _load(config)
    store = QdrantStore(settings)
    store.ensure()
    stats = store.stats()

    vocab_path = settings.paths.db_dir / "sparse_vocab.json"
    vocab_size = vocab_path.stat().st_size if vocab_path.exists() else 0

    table = Table(title="知识库信息", show_header=False)
    table.add_row("设备", settings.resolve_device())
    table.add_row("嵌入模型", f"{settings.embedding.model}（dim={settings.embedding.dim}）")
    table.add_row("重排模型", settings.rerank.model if settings.rerank.enabled else "关闭")
    table.add_row("稀疏方案", settings.sparse.method)
    table.add_row("文档数", str(stats["docs"]))
    table.add_row("切块数", str(stats["chunks"]))
    table.add_row("稀疏词表", f"{vocab_size / 1024:.0f} KB" if vocab_size else "（未使用）")
    table.add_row("数据库目录", str(settings.paths.db_dir))
    console.print(table)


def main() -> None:
    app()


if __name__ == "__main__":
    main()