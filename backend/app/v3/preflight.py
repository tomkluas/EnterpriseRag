# 离线配置预检：只检查生产配置形状和本地密钥引用，输出固定字段名与布尔结果。
# 预检通过不代表网络可达、部署隔离有效或底座已经通过业务验收。
"""离线检查生产配置，不代表连接可达或业务验收通过。"""
import os
import re
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


# 拒绝空白、通配主机、内嵌账号和片段，仅在调用方允许时接受查询串。
def _url(value, schemes, query=False):
    if not _text(value) or any(char.isspace() or ord(char) < 32 for char in value):
        return False
    try:
        parsed = urlsplit(value)
        return bool(parsed.scheme in schemes and parsed.hostname and '*' not in parsed.hostname
                    and parsed.port != 0 and parsed.username is None and parsed.password is None
                    and not parsed.fragment and (query or not parsed.query))
    except ValueError:
        return False


# 只解析支持的 PostgreSQL 连接串，不建立连接，也不返回可能含凭据的异常。
def _postgres(value):
    if not _text(value):
        return False
    try:
        parsed = make_url(value)
        return bool(parsed.drivername in ('postgresql', 'postgresql+psycopg2') and parsed.database)
    except (ValueError, TypeError, ArgumentError):
        # 解析器诊断可能包含输入连接串，不得返回给调用方，以免泄露数据库密码。
        return False


# 要求合法桶名及明确前缀，避免生产原件区域指向整个桶根目录。
def _s3(value):
    if not isinstance(value, str) or not _url(value, ('s3',)):
        return False
    parsed = urlsplit(value)
    bucket = parsed.netloc
    if not re.fullmatch(r'[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]', bucket) or '..' in bucket:
        return False
    prefix = parsed.path[1:].rstrip('/')
    return bool(prefix and '\\' not in prefix and ':' not in prefix
                and all(part not in ('', '.', '..') for part in prefix.split('/')))


# 仅检查规定格式的环境变量引用及对应值存在，不输出秘密内容。
def _reference(value, environ):
    return bool(isinstance(value, str) and re.fullmatch(r'V3_SECRET_[A-Z0-9_]+', value)
                and _text(environ.get(value)))


# 开发模式只验证模式值；生产要求身份认证、原件存储、模型配置完整且禁用引导令牌。
def check_settings(settings, environ=None) -> dict:
    """仅返回固定字段名和布尔值，不回显配置值或凭据。

    检查通过只说明配置格式及本地密钥引用有效。
    部署隔离、远端访问和业务验收仍需另行验证。
    """
    environ = os.environ if environ is None else environ
    checks = {'V3_MODE': settings.mode in ('production', 'development')}
    if settings.mode != 'development':
        # 配置独立精排地址时检查精排密钥引用，否则按模型密钥引用检查；不验证远端连通性。
        separate_reranker = bool(settings.rerank_base_url)
        checks.update({
            'V3_DATABASE_URL': _postgres(settings.database_url),
            'V3_BOOTSTRAP_TOKEN': settings.bootstrap_token == '',
            'V3_OIDC_ISSUER': _url(settings.issuer, ('https',)),
            'V3_OIDC_AUDIENCE': _text(settings.audience) and '*' not in settings.audience,
            'V3_OIDC_JWKS_URL': _url(settings.jwks_url, ('https',), query=True),
            'V3_OBJECTS_URI': _s3(settings.objects_dir),
            'V3_SECRET_S3_ACCESS': _text(environ.get('V3_SECRET_S3_ACCESS')),
            'V3_SECRET_S3_SECRET': _text(environ.get('V3_SECRET_S3_SECRET')),
            'V3_MODEL_BASE_URL': _url(settings.model_base_url, ('http', 'https')),
            'V3_MODEL_KEY_REF': _reference(settings.model_key_ref, environ),
            'V3_CHAT_MODEL': _text(settings.chat_model),
            'V3_RERANK_MODEL': _text(settings.rerank_model),
            'V3_RERANK_BASE_URL': not separate_reranker or _url(settings.rerank_base_url, ('http', 'https')),
            'V3_RERANK_KEY_REF': _reference(settings.rerank_key_ref if separate_reranker else settings.model_key_ref, environ),
        })
    errors = [field for field, passed in checks.items() if not passed]
    # 只返回固定配置项名和布尔值，不能把输入值或解析器异常中的连接密码回显出去。
    return {'passed': not errors, 'checks': checks, 'errors': errors}
