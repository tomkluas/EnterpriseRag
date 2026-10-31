import os
from dataclasses import dataclass
from pathlib import Path


@dataclass
class Settings:
    database_url: str = ''
    tenant_id: str = 'enterprise'
    mode: str = 'production'
    bootstrap_token: str = ''
    bootstrap_user: str = 'admin'
    issuer: str = ''
    audience: str = ''
    jwks_url: str = ''
    objects_dir: Path | str = Path('data-v3/objects')
    model_base_url: str = ''
    model_key_ref: str = 'V3_SECRET_MODEL'
    chat_model: str = ''
    rerank_model: str = ''
    rerank_base_url: str = ''
    rerank_key_ref: str = 'V3_SECRET_RERANK'

    @classmethod
    def from_env(cls):
        return cls(database_url=os.getenv('V3_DATABASE_URL',''),tenant_id=os.getenv('V3_TENANT_ID','enterprise'),
            mode=os.getenv('V3_MODE','production'),bootstrap_token=os.getenv('V3_BOOTSTRAP_TOKEN',''),
            bootstrap_user=os.getenv('V3_BOOTSTRAP_USER','admin'),issuer=os.getenv('V3_OIDC_ISSUER',''),
            audience=os.getenv('V3_OIDC_AUDIENCE',''),jwks_url=os.getenv('V3_OIDC_JWKS_URL',''),
            objects_dir=os.getenv('V3_OBJECTS_URI') or Path(os.getenv('V3_OBJECTS_DIR','data-v3/objects')).resolve(),
            model_base_url=os.getenv('V3_MODEL_BASE_URL',''),model_key_ref=os.getenv('V3_MODEL_KEY_REF','V3_SECRET_MODEL'),
            chat_model=os.getenv('V3_CHAT_MODEL',''),rerank_model=os.getenv('V3_RERANK_MODEL',''),
            rerank_base_url=os.getenv('V3_RERANK_BASE_URL',''),rerank_key_ref=os.getenv('V3_RERANK_KEY_REF','V3_SECRET_RERANK'))
