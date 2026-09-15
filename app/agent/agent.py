import os
import json

from .tools import tools_schema,tools_route
from .prompt import SYSTEM_PROMPT
from dotenv import load_dotenv
from openai import OpenAI


'Agent最大循环次数'
MAX_ROUNDS=5

def call_API(message):
    '''导入环境配置'''
    load_dotenv()

    client = OpenAI(
        api_key=os.getenv("LLM_API_KEY"),
        base_url=os.getenv("LLM_URL"))

    response = client.chat.completions.create(
        model="deepseek-flash",
        messages=message,
        tools=tools_schema.TOOLS_SCHEMA,
        stream=False,
        reasoning_effort="high",
        extra_body={"thinking": {"type": "enabled"}}
    )

    return response.choices[0].message

    #return

def chat_(message):
    send_message=[
        {"role":"system","content":SYSTEM_PROMPT},
        {"role":"user","content":message}
    ]
    for _ in range(MAX_ROUNDS):
        llm_response = call_API(send_message)

        if llm_response.tool_calls:

            send_message.append({
                "role": "assistant",
                "content": llm_response.content or "",
                "tool_calls": [
                    {
                        "id": tool_call.id,
                        "type": "function",
                        "function": {
                            "name": tool_call.function.name,
                            "arguments": tool_call.function.arguments
                        }
                    }
                    for tool_call in llm_response.tool_calls
                ]
            })

            for tool_call in llm_response.tool_calls:
                tool_name=tool_call.function.name
                result=tools_route.route(tool_name, tool_call.function.arguments)

                send_message.append({
                    "role":"tool",
                    "content":result,
                    "tool_call_id":tool_call.id
                })
        else:
            return llm_response.content


