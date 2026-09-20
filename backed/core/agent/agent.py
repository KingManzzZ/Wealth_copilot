import os
import json

from click import argument

from ..tools import tools_route, tools_schema
from .prompt import SYSTEM_PROMPT

from dotenv import load_dotenv
from openai import OpenAI
from openai.types.chat import ChatCompletionMessageParam


#Agent最大循环次数
MAX_ROUNDS=5
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
            try:
                arguments=json.loads(tool["arguments"]) if tool["arguments"] else {}
            except json.JSONDecodeError:
                arguments = {}
            yield {"type": "tool_call", "id": tool["id"], "name": tool["name"], "arguments": arguments}

            origin_result=tools_route.route(tool["name"], arguments)
            result = json.loads(origin_result)
            yield {"type": "tool_result", "id": tool["id"], "name": tool["name"], "result": result}

            messages.append({
                "role": "tool",
                "content": origin_result,
                "tool_call_id": tool["id"]
            })

    #跑满轮次兜底
    yield {"type": "error", "message": f"达到最大循环次数（{MAX_ROUNDS}），已中止"}
    yield {"type": "done"}