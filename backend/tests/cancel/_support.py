"""取消链路测试共用：路径引导、一次性数据库、假 LLM。

## 为什么每个用例都要独立数据库

回放/落库断言依赖"库里到底有什么"。若共享一个库，前一个用例写入的
``interrupted`` 行会让后一个用例的"回放条数"断言随机失败。这里用
``temp_database()`` 上下文管理器把 ``core.database.connection.DB_PATH`` 指到
一次性文件，退出时连同 ``-wal/-shm`` 一起删掉。

**不碰 ``backend/db/data.db``**：那是真实账本，测试永远不该写它。

## 为什么假 LLM 要可控延迟

取消的断言对象是"取消之后还会不会继续发请求/跑工具"。没有延迟时，假流会在
取消信号送到之前就跑完，测试退化成"跑得快所以通过"。所以 ``delay`` 是必需的。
"""

from __future__ import annotations

import os
import sys
import tempfile
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

TESTS_DIR = Path(__file__).resolve().parent
BACKEND_DIR = TESTS_DIR.parents[1]  # backend/

for path in (str(BACKEND_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

# ``core.agent.agent`` 在 import 时就构造 OpenAI 客户端；假客户端不会联网，
# 但构造仍要求 key 存在。必须在 import core 之前设好。
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_URL", "http://127.0.0.1:9")


def env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def test_tmp_dir() -> Path:
    """测试临时目录。

    优先 ``WP_TEST_TMP``：某些受限环境（如 DSH 沙箱）只允许写工作区内特定目录，
    系统临时目录不可写。默认走 ``tempfile.mkdtemp``，用完不删（交给系统清理）。
    """
    override = os.environ.get("WP_TEST_TMP")
    if override:
        base = Path(override)
        base.mkdir(parents=True, exist_ok=True)
        return base
    return Path(tempfile.mkdtemp(prefix="wp-cancel-test-"))


@contextmanager
def temp_database():
    """把数据库连接指向一次性文件；退出时恢复原路径并清理。"""
    import core.database.connection as connection

    db_path = test_tmp_dir() / f"data-{uuid.uuid4().hex}.db"
    previous_path = connection.DB_PATH
    previous_checked = connection._schema_checked
    connection.DB_PATH = str(db_path)
    connection._schema_checked = False
    try:
        yield db_path
    finally:
        connection.DB_PATH = previous_path
        connection._schema_checked = previous_checked
        for suffix in ("", "-wal", "-shm"):
            try:
                os.remove(f"{db_path}{suffix}")
            except OSError:
                pass


# ======================================================================
# 假 LLM：可控延迟的流式客户端
# ======================================================================


def delta(content=None, reasoning_content=None, tool_calls=None):
    return SimpleNamespace(
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
    )


def chunk(delta_obj):
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta_obj)])


def tool_call(index: int, call_id: str, name: str, args: str = "{}"):
    """工具调用 delta。``args`` 是原始 JSON 字符串（与 SDK 一致）。"""
    return SimpleNamespace(
        index=index,
        id=call_id,
        function=SimpleNamespace(name=name, arguments=args),
    )


class FakeStream:
    """可迭代、可 close 的假流；``delay`` 控制相邻 chunk 的间隔。"""

    def __init__(self, chunks, delay: float = 0.0):
        self._chunks = list(chunks)
        self._delay = delay
        self.closed = False
        self.iterated = 0

    def __iter__(self):
        for item in self._chunks:
            if self._delay:
                time.sleep(self._delay)
            self.iterated += 1
            yield item

    def close(self):
        self.closed = True


class FakeOpenAI:
    """按脚本依次返回流；记录调用次数与返回过的流。"""

    def __init__(self, script, delay: float = 0.0):
        self._script = [list(round_) for round_ in script]
        self._delay = delay
        self.calls: list[dict] = []
        self.streams: list[FakeStream] = []
        outer = self

        class _Completions:
            def create(self, **kwargs):
                outer.calls.append(kwargs)
                chunks = outer._script.pop(0) if outer._script else []
                stream = FakeStream(chunks, outer._delay)
                outer.streams.append(stream)
                return stream

        self.chat = SimpleNamespace(completions=_Completions())

    @property
    def call_count(self) -> int:
        return len(self.calls)


def answer_script(text: str = "好的"):
    return [[chunk(delta(content=text))]]


def tool_round_script(name: str, args: str = "{}", final: str = "完成"):
    """两轮：第一轮调工具，第二轮收尾。"""
    return [
        [chunk(delta(tool_calls=[tool_call(0, "call_1", name, args)]))],
        *answer_script(final),
    ]


def install_fake_client(fake):
    """把 ``core.agent.agent.client`` 换成假客户端，返回还原函数。"""
    from core.agent import agent as agent_mod

    original = agent_mod.client
    agent_mod.client = fake

    def restore() -> None:
        agent_mod.client = original

    return restore
