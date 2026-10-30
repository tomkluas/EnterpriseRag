async def router_node(state: dict) -> dict:
    """
    路由节点：仅做路由判断（实际路由逻辑在 graph.py 的 conditional_edges 里），
    不需要修改业务数据。

    注意：LangGraph 要求节点返回值必须至少写入 state schema 中的一个字段，
    不能返回空字典 {}（会报 "Must write to at least one of [...]" 错误）。
    这里显式写入 error=None 作为无害的占位更新。
    """
    return {"error": None}
