# V3 配置集中入口：字段保存连接参数和密钥引用，实际密钥由部署环境提供。
# from_env 只读取 V3_* 变量，防止混用旧版数据库、对象目录或身份配置。

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
# 集中传递 API、Worker、模型服务与身份验证所需配置。
class Settings:
    database_url: str = ''  # V3 独立业务数据库连接，不复用知识服务内部库
    tenant_id: str = 'enterprise'  # 当前公司租户边界，由服务端固定配置
    mode: str = 'production'  # development 仅用于受限开发/本机试运行，其他模式执行生产预检
    bootstrap_token: str = ''  # 仅开发模式识别的启动令牌；生产配置不得携带
    bootstrap_user: str = 'admin'  # 启动初始化和开发令牌所对应的本地成员账号
    issuer: str = ''  # 企业令牌受信任签发者
    audience: str = ''  # 本 API 接受的令牌受众，防止使用发给其他服务的令牌
    jwks_url: str = ''  # 取得 RS256 签名公钥的 HTTPS 地址
    objects_dir: Path | str = Path('data-v3/objects')  # 自有原件目录或 S3 URI，独立于远端索引副本
    model_base_url: str = ''  # 改写与回答生成使用的模型服务基础地址
    model_key_ref: str = 'V3_SECRET_MODEL'  # 模型密钥所在环境变量名称
    chat_model: str = ''  # 改写与生成使用的模型标识
    rerank_model: str = ''  # 统一证据精排使用的模型标识
    rerank_base_url: str = ''  # 可独立配置的精排服务地址
    rerank_key_ref: str = 'V3_SECRET_RERANK'  # 精排服务密钥所在环境变量名称

    @classmethod
    # 构造本进程的配置快照；对象 URI 优先于本地对象目录。
    def from_env(cls):
        return cls(database_url=os.getenv('V3_DATABASE_URL',''),tenant_id=os.getenv('V3_TENANT_ID','enterprise'),
            mode=os.getenv('V3_MODE','production'),bootstrap_token=os.getenv('V3_BOOTSTRAP_TOKEN',''),
            bootstrap_user=os.getenv('V3_BOOTSTRAP_USER','admin'),issuer=os.getenv('V3_OIDC_ISSUER',''),
            audience=os.getenv('V3_OIDC_AUDIENCE',''),jwks_url=os.getenv('V3_OIDC_JWKS_URL',''),
            objects_dir=os.getenv('V3_OBJECTS_URI') or Path(os.getenv('V3_OBJECTS_DIR','data-v3/objects')).resolve(),
            model_base_url=os.getenv('V3_MODEL_BASE_URL',''),model_key_ref=os.getenv('V3_MODEL_KEY_REF','V3_SECRET_MODEL'),
            chat_model=os.getenv('V3_CHAT_MODEL',''),rerank_model=os.getenv('V3_RERANK_MODEL',''),
            rerank_base_url=os.getenv('V3_RERANK_BASE_URL',''),rerank_key_ref=os.getenv('V3_RERANK_KEY_REF','V3_SECRET_RERANK'))
