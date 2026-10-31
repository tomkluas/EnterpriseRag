"""Offline production configuration checks. No connectivity or acceptance claims."""
import os
import re
from urllib.parse import urlsplit

from sqlalchemy.engine import make_url
from sqlalchemy.exc import ArgumentError


def _text(value):
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


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


def _postgres(value):
    if not _text(value):
        return False
    try:
        parsed = make_url(value)
        return bool(parsed.drivername in ('postgresql', 'postgresql+psycopg2') and parsed.database)
    except (ValueError, TypeError, ArgumentError):
        # Parser diagnostics may contain the input connection string. Never return them.
        return False


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


def _reference(value, environ):
    return bool(isinstance(value, str) and re.fullmatch(r'V3_SECRET_[A-Z0-9_]+', value)
                and _text(environ.get(value)))


def check_settings(settings, environ=None) -> dict:
    """Return only fixed field names and booleans; never echo settings or credentials.

    A passing result checks shape and locally available secret references only.
    Deployment isolation, remote access and business acceptance remain unverified.
    """
    environ = os.environ if environ is None else environ
    checks = {'V3_MODE': settings.mode in ('production', 'development')}
    if settings.mode != 'development':
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
    return {'passed': not errors, 'checks': checks, 'errors': errors}
