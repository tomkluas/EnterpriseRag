from app.core.security import decrypt
from app.db import crud


def get_search_provider(db):
    """从数据库（实际存于 config.json）读取用户配置的搜索服务商，
    无配置则返回 None（web_node 据此跳过联网搜索）"""
    cfg = crud.get_search_config(db)
    if not cfg or not cfg.get("api_key_enc"):
        return None
    api_key = decrypt(cfg["api_key_enc"])
    if cfg["provider"] == "bocha":
        from app.search.bocha_search import BochaSearchProvider
        return BochaSearchProvider(api_key)
    elif cfg["provider"] == "tavily":
        from app.search.tavily_search import TavilySearchProvider
        return TavilySearchProvider(api_key)
    return None
