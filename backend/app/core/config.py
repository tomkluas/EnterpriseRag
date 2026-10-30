from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # 应用
    app_name: str = "企业RAG系统"
    app_env: str = "production"

    # 安全
    secret_key: str
    encryption_key: str
    access_token_expire_minutes: int = 480

    # 系统数据库
    database_url: str = "sqlite:///./data/rag_system.db"

    # Qdrant
    qdrant_host: str = "qdrant"
    qdrant_port: int = 6333
    qdrant_api_key: str = ""

    # BGE 模型
    bge_model_path: str = "BAAI/bge-m3"
    bge_reranker_path: str = "BAAI/bge-reranker-v2-m3"
    bge_device: str = "cpu"
    bge_max_batch_size: int = 32

    # 默认大模型
    default_llm_provider: str = "deepseek"
    default_llm_model: str = "deepseek-reasoner"
    deepseek_api_key: str = ""
    deepseek_api_base: str = "https://api.deepseek.com"

    # 联网搜索
    tavily_api_key: str = ""
    tavily_max_results: int = 5

    # 文件上传
    upload_dir: str = "./data/uploads"
    chunk_size: int = 512
    chunk_overlap: int = 64
    max_file_size_mb: int = 50

    # LangSmith
    langchain_tracing_v2: bool = False
    langchain_api_key: str = ""
    langchain_project: str = "rag-system"

    # 初始管理员
    init_admin_username: str = "admin"
    init_admin_password: str = "Admin@ChangeMe123"


settings = Settings()