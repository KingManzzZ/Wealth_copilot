"""Agent 循环的取消检查点。

四个位置各有一条用例：

- 开跑前 → 一次 LLM 都不该发；
- 流式中途 → 上游流必须 ``close()``，且不能进入下一轮；
- 工具循环中 → 已执行的工具结果要保留（它已经产生副作用），但后续工具与下一轮不再发生；
- 不取消时 → 行为与改造前一致（防止"为了取消把正常路径改坏"）。

全部使用假 LLM / 假工具，不联网、不碰数据库。
"""

import threading
import time

import _support
from run_tests import eq, in_, true

from core.agent import agent
from core.agent.cancel import TurnCancel, TurnCancelled


def _slow_answer_script(n: int = 20, delay: float = 0.01):
    """``n`` 个回答 chunk，每个之间隔 ``delay``。"""
    return [[_support.chunk(_support.delta(content=f"字{i}")) for i in range(n)]]


def check_cancel_before_first_round_sends_no_request():
    fake = _support.FakeOpenAI(_slow_answer_script(5))
    restore = _support.install_fake_client(fake)
    try:
        turn = TurnCancel("t-pre", 1)
        turn.cancel("client")
        raised = False
        try:
            for _ in agent.chat_stream("你好", [], cancel=turn):
                pass
        except TurnCancelled:
            raised = True
        true(raised, "开跑前取消应抛 TurnCancelled")
        eq(fake.call_count, 0, "开跑前取消不应发起 LLM 请求")
    finally:
        restore()


def check_cancel_mid_stream_closes_upstream():
    fake = _support.FakeOpenAI(_slow_answer_script(40, delay=0.01))
    restore = _support.install_fake_client(fake)
    try:
        turn = TurnCancel("t-mid", 1)
        events = []
        raised = False
        try:
            for event in agent.chat_stream("你好", [], cancel=turn):
                events.append(event)
                answers = [e for e in events if e["type"] == "answer"]
                if len(answers) == 2:
                    turn.cancel("client")
        except TurnCancelled:
            raised = True

        true(raised, "流中取消应抛 TurnCancelled")
        answers = [e for e in events if e["type"] == "answer"]
        eq(len(answers), 2, "取消后不应再产出新的回答 chunk")
        eq(fake.call_count, 1, "取消后不应再发起第二轮 LLM 请求")
        true(fake.streams[0].closed, "上游流必须被 close（否则供应商侧继续计费）")
        true(fake.streams[0].iterated <= 3, "取消后不应继续消费上游流")
    finally:
        restore()


def check_cancel_during_tools_skips_rest_and_next_round():
    route_calls = []
    started = threading.Event()
    original_route = agent.tools_route.route

    def fake_route(name, arguments):
        route_calls.append(name)
        started.set()
        time.sleep(0.05)  # 模拟一次 1~3s 的检索（缩短版）
        return '{"ok": true}'

    agent.tools_route.route = fake_route
    fake = _support.FakeOpenAI(
        [
            [
                _support.chunk(
                    _support.delta(
                        tool_calls=[
                            _support.tool_call(0, "call_a", "get_monthly_cashflow"),
                            _support.tool_call(1, "call_b", "calculate_savings_rate"),
                        ]
                    )
                )
            ],
            _support.answer_script("不该出现"),
        ]
    )
    restore = _support.install_fake_client(fake)
    try:
        turn = TurnCancel("t-tool", 1)

        def cancel_soon():
            started.wait(2)
            turn.cancel("client")

        threading.Thread(target=cancel_soon, daemon=True).start()

        events = []
        raised = False
        try:
            for event in agent.chat_stream("查一下", [], cancel=turn):
                events.append(event)
        except TurnCancelled:
            raised = True

        true(raised, "工具循环中取消应抛 TurnCancelled")
        eq(route_calls, ["get_monthly_cashflow"], "只应执行第一个工具")
        eq(fake.call_count, 1, "不应再发起下一轮 LLM 请求")

        results = [e for e in events if e["type"] == "tool_result"]
        eq([r["id"] for r in results], ["call_a"], "已执行的工具结果必须保留（副作用已发生）")
    finally:
        restore()
        agent.tools_route.route = original_route


def check_tool_result_is_emitted_for_started_tool():
    """不取消时，工具结果照常产出——防止"检查点写成了一刀切"。"""
    original_route = agent.tools_route.route
    agent.tools_route.route = lambda name, arguments: '{"ok": true}'
    fake = _support.FakeOpenAI([[
        _support.chunk(_support.delta(tool_calls=[_support.tool_call(0, "call_ok", "get_monthly_cashflow")])),
    ], *_support.answer_script("完成")])
    restore = _support.install_fake_client(fake)
    try:
        events = list(agent.chat_stream("查一下", [], cancel=None))
        results = [e for e in events if e["type"] == "tool_result"]
        eq(len(results), 1, "应产出一条工具结果")
        eq(results[0]["status"], "success", "工具结果状态应为 success")
        eq(fake.call_count, 2, "两轮 LLM 请求（工具轮 + 收尾轮）")
        in_({"type": "done"}, events, "正常结束应产出 done 事件")
    finally:
        restore()
        agent.tools_route.route = original_route


def check_normal_run_has_no_turn_cancelled():
    fake = _support.FakeOpenAI(_support.answer_script("你好呀"))
    restore = _support.install_fake_client(fake)
    try:
        events = list(agent.chat_stream("你好", [], cancel=TurnCancel("t-normal", 1)))
        eq(fake.call_count, 1, "无工具调用时应只请求一次")
        eq([e["type"] for e in events if e["type"] == "answer"], ["answer"])
        eq(events[-1]["type"], "done", "最后一个事件应是 done")
    finally:
        restore()
