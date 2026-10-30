from typing import TypedDict, Annotated
import operator
from langgraph.graph import StateGraph, END, START

from app.agents.router_node import router_node
from app.agents.vector_node import vector_node
from app.agents.db_node import db_node
from app.agents.web_node import web_node
from app.agents.rerank_node import rerank_node
from app.agents.generate_node import generate_node


class RetrievalResult(TypedDict):
    text: str
    source_type: str    # "vector" | "database" | "web"
    source_name: str
    source_detail: str
    score: float


class RAGState(TypedDict):
    # 请求输入
    query: str
    session_id: str
    scope_vector: bool
    scope_database: bool
    scope_web: bool
    knowledge_ids: list[str]
    db_connection_ids: list[str]
    llm_config_id: str | None

    # 并行节点追加（Annotated + operator.add 实现并发合并）
    retrieval_results: Annotated[list[RetrievalResult], operator.add]
    retrieval_errors: Annotated[list[str], operator.add]

    # 精排后
    reranked_results: list[RetrievalResult]

    error: str | None
    _db: object
    _system_prompt: str
    _user_message: str


def _route_fn(state: RAGState) -> list[str]:
    targets = []
    if state.get("scope_vector"):   targets.append("vector")
    if state.get("scope_database"): targets.append("database")
    if state.get("scope_web"):      targets.append("web")
    return targets or ["vector"]


def build_graph() -> StateGraph:
    builder = StateGraph(RAGState)

    builder.add_node("router", router_node)
    builder.add_node("vector", vector_node)
    builder.add_node("database", db_node)
    builder.add_node("web", web_node)
    builder.add_node("rerank", rerank_node)
    builder.add_node("generate", generate_node)

    # langgraph 0.2+: 用 START 替代 set_entry_point
    builder.add_edge(START, "router")

    builder.add_conditional_edges("router", _route_fn, {
        "vector": "vector",
        "database": "database",
        "web": "web",
    })

    builder.add_edge("vector", "rerank")
    builder.add_edge("database", "rerank")
    builder.add_edge("web", "rerank")
    builder.add_edge("rerank", "generate")
    builder.add_edge("generate", END)

    return builder.compile()


rag_graph = build_graph()
