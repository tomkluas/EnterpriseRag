"""
国内大模型统一调用工厂。

实现说明：原计划用 LiteLLM（langchain_community.chat_models.ChatLiteLLM）统一调用，
但该封装存在已知未修复的 bug——api_key/api_base 参数无法正确透传到底层请求，
会导致请求被当成裸 OpenAI 调用、报错要求设置 OPENAI_API_KEY 环境变量
（社区追踪：langchain-ai/langchain#14338, BerriAI/litellm#7573）。

改用 langchain_openai.ChatOpenAI 直连：所有目标服务商（智谱/通义/DeepSeek/豆包/
月之暗面）均为 OpenAI 兼容协议，ChatOpenAI 对 api_key/base_url 的参数传递路径
简单直接、经过充分验证，不存在上述问题。
"""
from langchain_openai import ChatOpenAI
from app.core.security import decrypt
from app.db import crud
import httpx

PROVIDER_BASE_URL = {
    "zhipu": "https://open.bigmodel.cn/api/paas/v4",
    "qwen": "https://dashscope.aliyuncs.com/compatible-mode/v1",
    "deepseek": "https://api.deepseek.com",
    "doubao": "https://ark.cn-beijing.volces.com/api/v3",
    "moonshot": "https://api.moonshot.cn/v1",
}

# 各服务商推荐模型（用于 Setup 向导默认填充）
PROVIDER_DEFAULT_MODEL = {
    "zhipu": "glm-4-plus",
    "qwen": "qwen-plus",
    "deepseek": "deepseek-chat",
    "doubao": "doubao-pro-32k",
    "moonshot": "moonshot-v1-8k",
}

PROVIDER_DISPLAY_NAME = {
    "zhipu": "智谱AI",
    "qwen": "通义千问",
    "deepseek": "DeepSeek",
    "doubao": "豆包",
    "moonshot": "月之暗面",
}


def get_llm(db, config_id: str | None = None) -> ChatOpenAI:
    """
    优先级：
      1. 传入 config_id → 读取对应配置
      2. None → 读取 is_default=True 的配置
      3. 无任何配置 → 抛出异常，提示用户前往设置页配置
    """
    cfg = crud.get_llm_config(db, config_id) if config_id else crud.get_default_llm_config(db)

    if not cfg:
        raise RuntimeError('尚未配置大模型，请先在设置页面配置大模型API Key')

    api_key = decrypt(cfg.api_key_enc) if cfg.api_key_enc else ""
    base_url = cfg.base_url or PROVIDER_BASE_URL.get(cfg.provider, "")

    return ChatOpenAI(
        model=cfg.model_string,
        api_key=api_key,
        base_url=base_url,
        max_tokens=cfg.max_tokens,
        temperature=cfg.temperature,
        streaming=True,
        timeout=httpx.Timeout(45.0, connect=5.0),
        max_retries=0,
    )
