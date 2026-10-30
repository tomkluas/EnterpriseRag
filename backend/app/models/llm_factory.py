from typing import Optional

from langchain_community.chat_models import ChatLiteLLM

from app.core.config import settings
from app.core.security import decrypt
from app.db.crud import get_llm_config, get_default_llm_config
from app.db.models import LLMConfig


def get_llm(db, config_id: Optional[str] = None) -> ChatLiteLLM:
    """
    获取 LLM 实例。

    优先级：
    1. 传入 config_id → 从 DB 读取对应配置
    2. config_id=None → 读取 is_default=True 的配置
    3. DB 无配置 → 使用 .env 中的 DeepSeek 默认值
    """
    if config_id:
        db_config = get_llm_config(db, config_id)
        if db_config is None:
            raise ValueError("模型配置不存在，请重新选择")
    else:
        db_config = get_default_llm_config(db)

    if db_config:
        return _build_llm_from_config(db_config)

    # 使用 .env 默认值
    return ChatLiteLLM(
        model="openai/deepseek-reasoner",
        model_kwargs={"api_key": settings.deepseek_api_key},
        api_base=settings.deepseek_api_base,
        streaming=True,
        max_tokens=4096,
        temperature=0.7,
    )


def _build_llm_from_config(config: LLMConfig) -> ChatLiteLLM:
    api_key = decrypt(config.api_key_enc) if config.api_key_enc else ""

    if config.provider == "deepseek":
        model_string = config.model_string or "deepseek-reasoner"
        api_base = config.base_url or "https://api.deepseek.com"
        return ChatLiteLLM(
            model=f"openai/{model_string}",
            model_kwargs={"api_key": api_key},
            api_base=api_base,
            streaming=True,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    elif config.provider == "openai":
        return ChatLiteLLM(
            model=config.model_string,
            model_kwargs={"api_key": api_key},
            api_base=config.base_url,
            streaming=True,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    elif config.provider == "ollama":
        return ChatLiteLLM(
            model=f"ollama/{config.model_string}",
            api_base=config.base_url or "http://ollama:11434",
            streaming=True,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
    else:
        # custom provider
        return ChatLiteLLM(
            model=config.model_string,
            model_kwargs={"api_key": api_key},
            api_base=config.base_url,
            streaming=True,
            max_tokens=config.max_tokens,
            temperature=config.temperature,
        )
