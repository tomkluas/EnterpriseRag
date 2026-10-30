from langgraph.graph import StateGraph, END

from app.agents.state import RAGState


def build_graph():
    from app.agents.vector_node import vector_node
    from app.agents.db_node import db_node
    from app.agents.web_node import web_node
    from app.agents.rerank_node import rerank_node

    builder = StateGraph(RAGState)

    # 添加节点
    builder.add_node("router", lambda state: state)  # 路由节点，不做实际处理
    builder.add_node("vector", vector_node)
    builder.add_node("database", db_node)
    builder.add_node("web", web_node)
    builder.add_node("rerank", rerank_node)

    def route_edges(state: RAGState):
        targets = []
        if state.get("scope_vector"):
            targets.append("vector")
        if state.get("scope_database"):
            targets.append("database")
        if state.get("scope_web"):
            targets.append("web")
        if not targets:
            targets = ["vector"]
        return targets

    # 设置条件路由
    builder.add_conditional_edges(
        "router",
        route_edges,
        {
            "vector": "vector",
            "database": "database",
            "web": "web",
        }
    )

    # 所有检索节点汇入 rerank
    builder.add_edge("vector", "rerank")
    builder.add_edge("database", "rerank")
    builder.add_edge("web", "rerank")
    builder.add_edge("rerank", END)

    builder.set_entry_point("router")

    return builder.compile()