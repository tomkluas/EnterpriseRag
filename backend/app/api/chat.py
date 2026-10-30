import json
import asyncio
import time
from contextlib import suppress
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from sqlalchemy.orm import Session

from app.core.deps import get_db
from app.core.auth import get_current_user
from app.db.models import User
from app.models.llm_factory import get_llm
from app.core.logging_config import get_logger

chat_logger = get_logger("api.chat")
router = APIRouter()


class ChatRequest(BaseModel):
    session_id: str
    query: str = Field(min_length=1, max_length=20000)
    scope_vector: bool = True
    scope_database: bool = False
    scope_web: bool = False
    knowledge_ids: list[str] = []
    db_connection_ids: list[str] = []
    llm_config_id: str | None = None

    @field_validator("query")
    @classmethod
    def nonblank_query(cls, value):
        value = value.strip()
        if not value:
            raise ValueError("请输入问题")
        return value


RETRIEVAL_TIMEOUT = 35
TOKEN_TIMEOUT = 45
CHAT_TIMEOUT = 180
HEARTBEAT_INTERVAL = 5


def sse_event(event: str, data) -> str:
    return f"data: {json.dumps({'event': event, 'data': data}, ensure_ascii=False)}\n\n"


@router.post("/stream")
async def chat_stream(
    req: ChatRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    async def generate():
        pending = None
        stream = None
        started = time.monotonic()
        try:
            yield sse_event("status", "正在准备回答")
            from app.agents.graph import rag_graph

            llm = get_llm(db, req.llm_config_id)
            chat_logger.info("对话开始 session=%s，检索范围 vector=%s database=%s web=%s", req.session_id, req.scope_vector, req.scope_database, req.scope_web)

            initial_state = {
                "query": req.query,
                "session_id": req.session_id,
                "scope_vector": req.scope_vector,
                "scope_database": req.scope_database,
                "scope_web": req.scope_web,
                "knowledge_ids": req.knowledge_ids,
                "db_connection_ids": req.db_connection_ids,
                "llm_config_id": req.llm_config_id,
                "retrieval_results": [],
                "retrieval_errors": [],
                "reranked_results": [],
                "error": None,
                "_db": db,
            }

            pending = asyncio.create_task(asyncio.wait_for(rag_graph.ainvoke(initial_state), RETRIEVAL_TIMEOUT))
            yield sse_event("status", "正在检索资料" if any([req.scope_vector, req.scope_database, req.scope_web]) else "正在连接模型")
            while not pending.done():
                await asyncio.wait({pending}, timeout=HEARTBEAT_INTERVAL)
                if not pending.done():
                    yield ": heartbeat\n\n"
            final_state = pending.result()
            pending = None
            errors = final_state.get("retrieval_errors", [])
            if errors and not final_state.get("reranked_results"):
                yield sse_event("error", "；".join(errors))
                return
            for error in errors:
                yield sse_event("warning", error)

            # Emit source events
            for source in final_state.get("reranked_results", []):
                yield sse_event("source", source)

            # Stream generation
            system_prompt = final_state.get("_system_prompt", "")
            user_message = final_state.get("_user_message", req.query)

            if not user_message:
                yield sse_event("error", "未能构建查询上下文")
                return

            from langchain_core.messages import SystemMessage, HumanMessage
            messages = [
                SystemMessage(content=system_prompt),
                HumanMessage(content=user_message),
            ]

            total_tokens = 0
            yield sse_event("status", "正在生成回答")
            stream = llm.astream(messages).__aiter__()
            while True:
                remaining = CHAT_TIMEOUT - (time.monotonic() - started)
                if remaining <= 0:
                    raise TimeoutError("对话总时限已到")
                pending = asyncio.create_task(asyncio.wait_for(anext(stream), min(TOKEN_TIMEOUT, remaining)))
                while not pending.done():
                    await asyncio.wait({pending}, timeout=HEARTBEAT_INTERVAL)
                    if not pending.done():
                        yield ": heartbeat\n\n"
                try:
                    chunk = pending.result()
                except StopAsyncIteration:
                    break
                pending = None
                token = chunk.content
                if token:
                    total_tokens += len(token)
                    yield sse_event("token", token)

            if not total_tokens:
                yield sse_event("error", "模型未返回有效回答，请检查模型配置后重试")
                return
            chat_logger.info("对话完成 session=%s，字符数=%d，耗时=%.2fs", req.session_id, total_tokens, time.monotonic() - started)
            yield sse_event("done", {"total_tokens": total_tokens})

        except (TimeoutError, asyncio.TimeoutError):
            chat_logger.warning("对话超时 session=%s，耗时=%.2fs", req.session_id, time.monotonic() - started)
            yield sse_event("error", "请求超时，请检查模型或检索服务连接后重试；也可关闭检索范围测试纯模型对话")
        except Exception as e:
            chat_logger.exception("对话失败 session=%s", req.session_id)
            yield sse_event("error", str(e))
        finally:
            if pending is not None:
                pending.cancel()
                with suppress(asyncio.CancelledError, Exception):
                    await pending
            if stream is not None and hasattr(stream, "aclose"):
                with suppress(asyncio.CancelledError, Exception):
                    await stream.aclose()

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
        },
    )
