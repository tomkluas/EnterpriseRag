import operator
from typing import Annotated, TypedDict


class RetrievalResult(TypedDict):
    text: str
    source_type: str
    source_name: str
    source_detail: str
    score: float


def merge_errors(left: str | None, right: str | None) -> str | None:
    if not left:
        return right
    if not right or right == left:
        return left
    return f"{left}；{right}"


class RAGState(TypedDict):
    query: str
    session_id: str
    scope_vector: bool
    scope_database: bool
    scope_web: bool
    knowledge_ids: list[str]
    db_connection_ids: list[str]
    llm_config_id: str | None
    retrieval_results: Annotated[list[RetrievalResult], operator.add]
    reranked_results: list[RetrievalResult]
    error: Annotated[str | None, merge_errors]
