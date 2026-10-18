"""取消令牌的语义：幂等、先到先得、检查点抛控制流异常、完成信号。

这一层不碰 LLM、不碰数据库，跑得最快，也最该先跑——取消链路的其余部分都建立在
"``cancel()`` 是幂等的、``check()`` 只抛控制流异常"这个前提上。
"""

from run_tests import eq, raises, true

from core.agent.cancel import TurnCancel, TurnCancelled


def check_initial_state():
    turn = TurnCancel("t-initial", 1)
    eq(turn.cancelled, False, "初始不应处于取消态")
    eq(turn.done, False, "初始不应处于完成态")
    eq(turn.reason, "", "初始没有 reason")
    eq(turn.cancelled_at, None, "初始没有取消时间戳")
    eq(turn.turn_id, "t-initial", "turn_id 应保留")


def check_cancel_is_idempotent():
    turn = TurnCancel("t-idem", 1)
    eq(turn.cancel("client"), True, "第一次取消应返回 True")
    eq(turn.cancel("client"), False, "重复取消应返回 False（幂等）")
    eq(turn.cancelled, True, "取消后状态应为 True")


def check_first_reason_wins():
    """先到先得：用户点停止的 reason 不应被随后的断连覆盖。"""
    turn = TurnCancel("t-reason", 1)
    turn.cancel("client")
    turn.cancel("disconnect")
    eq(turn.reason, "client", "reason 应保留第一次的值")


def check_check_raises_turn_cancelled():
    turn = TurnCancel("t-check", 1)
    turn.cancel("client")
    exc = raises(TurnCancelled, turn.check, "已取消时 check() 应抛 TurnCancelled")
    eq(exc.reason, "client", "异常应带上 reason")


def check_check_is_noop_before_cancel():
    turn = TurnCancel("t-noop", 1)
    turn.check()  # 不应抛任何异常
    true(not turn.cancelled, "check() 不应改变状态")


def check_wait_done_timeout_then_signal():
    turn = TurnCancel("t-wait", 1)
    eq(turn.wait_done(0.02), False, "未完成时 wait_done 应超时返回 False")
    turn.mark_done()
    eq(turn.wait_done(0.02), True, "标记完成后 wait_done 应立即返回 True")


def check_cancel_after_done_still_marks_cancelled():
    """收尾之后才到的取消请求：不应把已完成的轮次改写成"未完成"。

    ``wait_done`` 与 ``cancelled`` 是两个独立信号：worker 先落库（``cancelled`` 仍为
    False）再 ``mark_done``；如果取消恰好落在两者之间，``cancelled`` 变 True 但落库
    已经发生——所以路由层必须在**落库那一刻**读 cancelled，而不是事后再读。
    """
    turn = TurnCancel("t-late", 1)
    turn.mark_done()
    eq(turn.cancel("client"), True, "已完成的轮次仍可置位（调用方负责判断时序）")
    eq(turn.done, True, "done 不受影响")
