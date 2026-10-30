SYSTEM_PROMPT = """你是企业智能助手。请根据以下检索到的资料回答用户问题。
要求：
1. 只使用提供的资料，不要凭空捏造
2. 如资料不足以回答，明确说明"根据现有资料无法回答此问题"
3. 回答使用中文，结构清晰，适当使用列表或分段
4. 不需要在正文中重复来源标记，来源信息将在界面上单独展示
"""


def format_context(reranked_results: list) -> str:
    """将检索结果格式化为上下文"""
    if not reranked_results:
        return "（未找到相关资料）"

    context_parts = []
    for idx, result in enumerate(reranked_results, 1):
        source_info = f"[{result['source_type']}] {result['source_name']} - {result['source_detail']}"
        context_parts.append(f"【资料{idx}】来源: {source_info}\n{result['text']}")

    return "\n\n".join(context_parts)


async def generate_node(state: dict, llm) -> dict:
    """生成节点"""
    query = state["query"]
    reranked_results = state.get("reranked_results", [])

    context = format_context(reranked_results)

    user_prompt = f"""请根据以下资料回答问题。

问题：{query}

资料：
{context}
"""

    return {}  # 实际生成通过流式输出在 API 层处理