import os
import json

from ..tools import tools_route, tools_schema
from .prompt import SYSTEM_PROMPT

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam


#Agent最大循环次数
MAX_ROUNDS=5

#连续多少轮出现"未知工具"就判定模型无法自愈，提前收敛。
#取 2 是因为：同一个名字错一次后模型已经拿到了可用工具清单，
#第二次还错说明它没理解反馈，再试大概率仍是错的。
UNKNOWN_TOOL_LIMIT=2
load_dotenv()

client = OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_URL"))



def chat_stream(message, history=None):

    #messages为需要维护的总体消息列表
    messages:list[ChatCompletionMessageParam]=[
        {"role":"system","content":SYSTEM_PROMPT},
    ]

    if history:
        messages.extend(history)
    messages.append({"role":"user","content":message})

    #连续"未知工具"计数（跨轮次累计，命中 UNKNOWN_TOOL_LIMIT 即提前收敛）
    unknown_streak = 0
    #已执行过的 (工具名, 原始参数串)，用于识别"时机幻觉"——同参重复调用
    seen_calls: set[tuple[str, str]] = set()
    #最后一次失败的可读描述，供熔断时归因
    last_error = ""

    for round_num in range(1,MAX_ROUNDS+1):
        #向前端传递当前轮次情况
        yield {"type": "round", "round": round_num}

        # 用于拼接流式信息，组成完整信息维护messages
        content_parts = []
        tool_calls = {}

        stream = client.chat.completions.create(
            model="deepseek-flash",
            messages=messages,
            tools=tools_schema.TOOLS_SCHEMA,
            stream=True,
            reasoning_effort="high",
            extra_body={"thinking": {"type": "enabled"}}
        )

        #流式响应反馈处理
        try:
            for chunk in stream:
                if not chunk.choices:
                    continue
                delta=chunk.choices[0].delta

                #思考部分
                reasoning = getattr(delta, "reasoning_content", None)
                if reasoning:
                    yield {"type": "thinking", "content": reasoning}

                #回答部分
                if delta.content:
                    content_parts.append(delta.content)
                    yield {"type": "answer", "content": delta.content}

                #工具调用
                for tool_call in delta.tool_calls or []:
                    acc=tool_calls.setdefault(tool_call.index, {"id": "", "name": "", "arguments": ""})

                    if tool_call.id:
                        acc["id"]=tool_call.id
                    if tool_call.function:
                        if tool_call.function.name:
                            acc["name"]=tool_call.function.name
                        if tool_call.function.arguments:
                            acc["arguments"]+=tool_call.function.arguments

        finally:
            stream.close()


        #每轮工具调用维护，无工具调用则结束，有工具则后端开始执行
        if not tool_calls:
            yield {"type": "done"}
            return None

        #回填assistant消息
        messages.append({
            "role": "assistant",
            "content": "".join(content_parts),
            "tool_calls": [
                {
                    "id": tool["id"],
                    "type": "function",
                    "function": {
                        "name": tool["name"],
                        "arguments": tool["arguments"]
                    }
                }
                for tool in tool_calls.values()
            ]
        })

        #工具调用
        for tool in tool_calls.values():
            signature = (tool["name"], tool["arguments"] or "")
            if signature in seen_calls:
                #【时机幻觉】完全相同的调用重复出现，不再执行。
                # 仍要回填 role="tool" 消息：否则 tool_calls 与 tool 消息数量对不上，
                # 下一轮请求会被 API 拒绝。
                yield {"type": "tool_result", "id": tool["id"], "status": "error",
                       "result": {"error": "重复调用：与之前完全相同的工具调用，已跳过",
                                  "error_kind": "duplicate_call", "retryable": False}}
                messages.append({"role": "tool", "tool_call_id": tool["id"],
                                 "content": json.dumps({"error": "重复调用，已跳过"}, ensure_ascii=False)})
                continue
            seen_calls.add(signature)

            try:
                arguments=json.loads(tool["arguments"]) if tool["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            #route() 要求参数是 JSON 对象；这里同步降级，避免把 list/str 当成
            # arguments 推给前端（前端按 dict 处理会出错）。非法情况由 route() 报错。
            if not isinstance(arguments, dict):
                arguments = {}
            yield {"type": "tool_call", "id": tool["id"], "name": tool["name"], "arguments": arguments}

            #【进度事件】工具执行期间先通知前端，避免知识库检索（1~3s）表现为静默卡死。
            # 前端可据此显示"正在检索知识库…"，并在收到 tool_result 时收起。
            yield {"type": "tool_start", "id": tool["id"], "name": tool["name"]}

            origin_result=tools_route.route(tool["name"], tool["arguments"])
            result = json.loads(origin_result)
            status="error" if isinstance(result,dict) and "error" in result else "success"
            yield {"type": "tool_result", "id": tool["id"], "result": result, "status": status}

            #未知工具计数：成功一次就清零（模型已回到正轨）
            if isinstance(result, dict) and result.get("unknown_tool"):
                unknown_streak += 1
                last_error = f"调用了不存在的工具 {tool['name']}"
            elif status == "success":
                unknown_streak = 0

            messages.append({
                "role": "tool",
                "content": origin_result,
                "tool_call_id": tool["id"]
            })

        #【提前收敛】连续未知工具：模型已经拿到过可用工具清单还继续错名，
        # 说明它没理解反馈，再试只是烧轮次。这里给用户一句人话再收尾。
        if unknown_streak >= UNKNOWN_TOOL_LIMIT:
            yield {"type": "error",
                   "message": f"连续 {unknown_streak} 次调用了不存在的工具，已停止"}
            yield {"type": "answer",
                   "content": "抱歉，我在调用工具时遇到了问题（工具名无法匹配）。"
                              "能否再说明一下你具体想查询或想修改哪一项？我换个方式处理。"}
            yield {"type": "done"}
            return None

    #跑满轮次兜底。带上最后一次失败原因，否则用户和日志都看不出卡在哪一步。
    detail = f"；最后一次失败：{last_error}" if last_error else ""
    yield {"type": "error", "message": f"达到最大循环次数（{MAX_ROUNDS}）仍未能完成，已中止{detail}"}
    yield {"type": "done"}