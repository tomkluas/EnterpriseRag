"""
generate_node 仅构建 prompt 并存入 state，实际流式输出在 API 层完成。
"""

SYSTEM_PROMPT = """你是企业智能助手。请根据以下检索到的资料回答用户问题。
要求：
1. 只使用提供的资料，不要凭空捏造
2. 如资料不足以回答，明确说明"根据现有资料无法回答此问题"
3. 回答使用中文，结构清晰，适当使用列表或分段
4. 不需要在正文中重复来源标记，来源信息将在界面上单独展示"""


def build_context(results: list[dict]) -> str:
    if not results:
        return "（无检索结果）"
    parts = []
    for i, r in enumerate(results, 1):
        parts.append(f"[{i}] {r['text']}")
    return "\n\n".join(parts)


async def generate_node(state: dict) -> dict:
    """构建最终 prompt，存入 state 供 API 层流式调用"""
    results = state.get("reranked_results", [])
    query = state["query"]
    if not any(state.get(key) for key in ("scope_vector", "scope_database", "scope_web")):
        return {"_system_prompt": "你是企业智能助手。请用中文准确、清晰地回答用户的问题，不确定时说明不确定性。", "_user_message": query}
    context = build_context(results)
    user_message = f"参考资料：\n{context}\n\n用户问题：{query}"
    state["_system_prompt"] = SYSTEM_PROMPT
    state["_user_message"] = user_message
    return {
        "_system_prompt": SYSTEM_PROMPT,
        "_user_message": user_message,
    }
