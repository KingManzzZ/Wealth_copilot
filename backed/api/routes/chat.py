import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from core import chat_stream

router=APIRouter()


class ChatRequest(BaseModel):
    message: str
    history: list

def sse_(event:dict):
    return f"data: {json.dumps(event, ensure_ascii=False)}\n\n"


@router.post("/chat")
async def chat(request: ChatRequest):
    def event_stream():
        try:
            #迭代，相当于next
            for event in chat_stream(request.message, request.history):
                yield sse_(event)

        except Exception as e:
            yield sse_({"type": "error", "message": str(e)})

        finally:
            yield "data: [DONE]\n\n"

    return StreamingResponse(
        event_stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive"
        },)
