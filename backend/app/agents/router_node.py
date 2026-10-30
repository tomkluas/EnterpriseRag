from app.agents.state import RAGState


def _route_fn(state: RAGState) -> list[str]:
    targets = []
    if state["scope_vector"]:
        targets.append("vector")
    if state["scope_database"]:
        targets.append("database")
    if state["scope_web"]:
        targets.append("web")
    return targets or ["vector"]
