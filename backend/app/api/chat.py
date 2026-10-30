import json
from typing import AsyncGenerator

from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from langchain_core.messages import HumanMessage, SystemMessage
from sqlalchemy.orm import Session

from app.core.auth import get_current_user
from app.core.deps import get_db
from app.db.models import User
from app.agents.graph import build_graph
from app.agents.generate_node import SYSTEM_PROMPT, format_context
from app.models.llm_factory import get_llm

router = APIRouter(prefix="/api/v1/chat", tags=["对话"])


class ChatRequest(BaseModel):
    session_id: str = Field(min_length=1)
    query: str = Field(min_length=1)
    scope_vector: bool = True
    scope_database: bool = False
    scope_web: bool = False
    knowledge_ids: list[str] = Field(default_factory=list)
    db_connection_ids: list[str] = Field(default_factory=list)
    llm_config_id: str | None = None

    @field_validator("query", "session_id")
    @classmethod
    def strip_required_text(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("不能为空")
        return value


@router.post("/stream")
async def chat_stream(
    request: ChatRequest,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    # 构建初始状态
    initial_state = {
        **request.model_dump(),
        "retrieval_results": [],
        "reranked_results": [],
        "error": None,
    }

    async def event_generator() -> AsyncGenerator[str, None]:
        try:
            graph = build_graph()
            final_state = None
            async for chunk in graph.astream(initial_state, stream_mode="values"):
                final_state = chunk
            if final_state and final_state.get("error"):
                raise RuntimeError(final_state["error"])
            retrieval_results = final_state.get("reranked_results", []) if final_state else []
            # FastAPI closes yield dependencies before streaming in this version.
            # A model lookup can reopen the Session; release that connection immediately.
            try:
                llm = get_llm(db, request.llm_config_id)
            finally:
                if db is not None:
                    db.close()
            context = format_context(retrieval_results)
            user_prompt = f"请根据以下资料回答问题。\n\n问题：{request.query}\n\n资料：\n{context}\n"
            for result in retrieval_results:
                source_event = {"event": "source", "data": result}
                yield f"data: {json.dumps(source_event, ensure_ascii=False)}\n\n"
            full_response = ""
            messages = [SystemMessage(content=SYSTEM_PROMPT), HumanMessage(content=user_prompt)]
            async for chunk in llm.astream(messages):
                token = chunk.content
                if not token:
                    continue
                full_response += token
                token_event = {
                    "event": "token",
                    "data": token,
                }
                yield f"data: {json.dumps(token_event, ensure_ascii=False)}\n\n"

            done_event = {
                "event": "done",
                "data": {"total_tokens": len(full_response)},
            }
            yield f"data: {json.dumps(done_event, ensure_ascii=False)}\n\n"
        except Exception as e:
            error_event = {
                "event": "error",
                "data": str(e),
            }
            yield f"data: {json.dumps(error_event, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
