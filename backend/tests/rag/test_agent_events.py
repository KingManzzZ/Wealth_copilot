"""Agent 事件流测试（``core/agent/agent.py`` + ``api/routes/chat.py``）。

用**假 OpenAI 客户端**驱动 `chat_stream`，验证 SSE 事件序列与工具回填逻辑。
不访问网络、不加载模型。

**为什么值得测**：`chat_stream` 是生成器，错误只会表现为"少了一个事件"——
前端不会报错，只是静默不显示。而事件序列是前端状态机（工具卡片何时出现、
何时收起）的唯一依据，所以序列本身是需要锁住的契约。

对应 `问题文档/问题与解决方案.md` 的 P2-16（工具执行期间缺进度反馈）。
"""

from __future__ import annotations

import json

import _support
from run_tests import Skip, eq, in_, true


# ======================================================================
# 假 OpenAI 客户端
# ======================================================================
#
# 实现已收敛到 ``_support.py``（与 test_api_e2e 共用同一份，避免两处各抄一份）。
# 这里保留原用例内的短名字，便于阅读与最小化改动。

FakeOpenAI = _support.FakeOpenAI
_chunk = _support.chunk
_Delta = _support.Delta
_tool_call_delta = _support.tool_call_delta
_answer_only = _support.answer_script
_one_tool_round = _support.tool_round_script


# ======================================================================
# 加载 agent（需要 openai 且能构造客户端）
# ======================================================================


def _load_agent(fake_client):
    try:
        from core.agent import agent as agent_mod
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"无法 import agent（缺少 openai 或环境变量？）：{type(exc).__name__}: {exc}")

    # 替换模块级客户端；不需要真的 API key
    agent_mod.client = fake_client
    return agent_mod


def _collect(gen):
    return list(gen)


# ======================================================================
# 基础事件序列
# ======================================================================

def check_answer_only_sequence():
    fake = FakeOpenAI([_answer_only("你好，我是 WealthPilot。")])
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("你好"))

    types = [e["type"] for e in events]
    eq(types[0], "round", "首个事件应是 round")
    in_("thinking", types, "应转发思考内容")
    in_("answer", types, "应转发回答内容")
    eq(types[-1], "done", "最后一个事件应是 done")
    eq(types.count("done"), 1, "done 只应出现一次")


def check_done_when_no_tool_calls():
    fake = FakeOpenAI([_answer_only("好的")])
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("随便问问"))
    not_tool = [e for e in events if e["type"] in ("tool_call", "tool_start", "tool_result")]
    eq(not_tool, [], "无工具调用时不应出现工具事件")


# ======================================================================
# 工具事件序列（核心契约）
# ======================================================================

def check_tool_event_order():
    """tool_call → tool_start → tool_result 的顺序是前端状态机的依据。"""
    fake = FakeOpenAI(
        _one_tool_round("this_tool_does_not_exist", "{}", "我没查到")
    )
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("查点东西"))

    idx = {
        name: [i for i, e in enumerate(events) if e["type"] == name]
        for name in ("tool_call", "tool_start", "tool_result")
    }
    true(len(idx["tool_call"]) == 1, "应有 1 个 tool_call")
    true(len(idx["tool_start"]) == 1, "应有 1 个 tool_start（进度事件）")
    true(len(idx["tool_result"]) == 1, "应有 1 个 tool_result")
    true(
        idx["tool_call"][0] < idx["tool_start"][0] < idx["tool_result"][0],
        f"顺序应为 call < start < result，实际 {idx}",
    )


def check_tool_start_carries_identity():
    """tool_start 必须带 id 与 name，前端才能对上工具卡片。"""
    fake = FakeOpenAI(_one_tool_round("this_tool_does_not_exist", "{}"))
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("q"))
    start = next(e for e in events if e["type"] == "tool_start")
    eq(start["id"], "call_1")
    eq(start["name"], "this_tool_does_not_exist")


def check_tool_error_status():
    """未知工具 → tool_result 的 status 应为 error（前端据此标红）。"""
    fake = FakeOpenAI(_one_tool_round("nope_not_a_tool", "{}"))
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("q"))
    result = next(e for e in events if e["type"] == "tool_result")
    eq(result["status"], "error")
    in_("error", result["result"])


def check_search_knowledge_error_status_when_unavailable():
    """search_knowledge 在 RAG 不可用时也必须让 status=error。

    这是真实的降级路径：模型调用检索工具 → RAG 依赖缺失 → 返回 error 载荷
    → agent 判定 status=error → 前端可提示"检索不可用"，模型也能据此改口。
    """
    from core.tools import tools_route

    raw = tools_route.route("search_knowledge", json.dumps({"query": ""}))
    payload = json.loads(raw)
    in_("error", payload, "空 query 应返回 error 载荷")


def check_arguments_parsed_in_tool_call_event():
    """tool_call 事件里的 arguments 应是**已解析的 dict**（不是原始字符串）。"""
    fake = FakeOpenAI(_one_tool_round("this_tool_does_not_exist", '{"a": 1}'))
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("q"))
    call = next(e for e in events if e["type"] == "tool_call")
    eq(call["arguments"], {"a": 1})
    true(isinstance(call["arguments"], dict), "arguments 应为 dict")


def check_bad_json_arguments_do_not_crash():
    """工具参数不是合法 JSON 时不应抛异常，应降级为 {}。"""
    fake = FakeOpenAI(_one_tool_round("this_tool_does_not_exist", "{坏掉的 json"))
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("q"))
    call = next(e for e in events if e["type"] == "tool_call")
    eq(call["arguments"], {}, "非法 JSON 应降级为空 dict")
    # 仍应走完整个流程
    eq(events[-1]["type"], "done")


# ======================================================================
# 消息回填
# ======================================================================

def check_tool_result_backfilled_into_messages():
    """工具结果必须以 role="tool" 回填，否则模型看不到证据。"""
    fake = FakeOpenAI(_one_tool_round("this_tool_does_not_exist", "{}"))
    mod = _load_agent(fake)
    _collect(mod.chat_stream("q"))

    # 第二次请求的 messages 里应含 assistant(tool_calls) 与 tool 消息
    second = fake.requests[1]["messages"]
    roles = [m["role"] for m in second]
    in_("assistant", roles)
    in_("tool", roles)

    tool_msg = next(m for m in second if m["role"] == "tool")
    eq(tool_msg["tool_call_id"], "call_1")
    true(isinstance(tool_msg["content"], str), "tool 消息的 content 应是字符串（JSON 文本）")
    json.loads(tool_msg["content"])  # 应可解析


def check_history_prepended():
    fake = FakeOpenAI([_answer_only("嗯")])
    mod = _load_agent(fake)
    history = [
        {"role": "user", "content": "之前的问题"},
        {"role": "assistant", "content": "之前的回答"},
    ]
    _collect(mod.chat_stream("新的问题", history=history))
    msgs = fake.requests[0]["messages"]
    eq(msgs[0]["role"], "system")
    eq(msgs[1]["content"], "之前的问题", "history 应被插入到 system 之后")
    eq(msgs[-1]["content"], "新的问题", "当前输入应在最后")


def check_max_rounds_guard():
    """模型无限调工具时必须中止（不能死循环）。"""
    # 每轮都只有一个工具调用，永不结束
    script = [
        [_chunk(_Delta(tool_calls=[_tool_call_delta(0, f"call_{i}", "this_tool_does_not_exist", "{}")]))]
        for i in range(20)
    ]
    fake = FakeOpenAI(script)
    mod = _load_agent(fake)
    events = _collect(mod.chat_stream("死循环"))
    types = [e["type"] for e in events]
    in_("error", types, "跑满轮次应报错中止")
    err = next(e for e in events if e["type"] == "error")
    in_(str(mod.MAX_ROUNDS), err["message"], "错误信息应包含最大轮次")
    eq(types[-1], "done")
    true(len(events) < 200, f"事件数应有界（实际 {len(events)}）")
    eq(types.count("done"), 1, "done 只应出现一次")


def check_stream_closed():
    """每轮流式响应都应被 close（否则连接泄漏）。"""
    fake = FakeOpenAI([_answer_only("好")])
    mod = _load_agent(fake)
    _collect(mod.chat_stream("q"))
    true(all(s.closed for s in fake.streams), "所有流都应被关闭")


# ======================================================================
# SSE 序列化
# ======================================================================

def check_sse_frame_format():
    """chat.py 的 sse_ 必须产出合法 SSE 帧（前端按 \\n\\n 分帧）。"""
    try:
        from api.routes.chat import sse_
    except Exception as exc:  # noqa: BLE001
        raise Skip(f"无法 import chat 路由：{type(exc).__name__}: {exc}")

    frame = sse_({"type": "answer", "content": "你好"})
    true(frame.startswith("data: "), "SSE 帧应以 'data: ' 开头")
    true(frame.endswith("\n\n"), "SSE 帧应以空行结尾")
    payload = json.loads(frame[len("data: ") :].strip())
    eq(payload["type"], "answer")
    eq(payload["content"], "你好")
    # 中文不应被转义（ensure_ascii=False）
    in_("你好", frame, "中文不应被转义成 \\u 形式")
