from fastapi import APIRouter
from pydantic import BaseModel
from core import chat_

router=APIRouter()

# { "message": "我的月度现金流怎么样？",
#   "history": [
#       { "role": "user", "content": "..." }
#   ] }

class ChatRequest(BaseModel):
    message: str
    history: list

@router.post("/chat")
async def chat(request: ChatRequest):
    response = chat_(request.message)
    print(response)
