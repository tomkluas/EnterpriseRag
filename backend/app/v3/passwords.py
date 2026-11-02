# 本机账号密码登录：摘要凭据、短期会话与登录限速均按租户保存。
# 会话令牌明文只在登录响应中返回，数据库仅保存摘要；成员接口不返回密码摘要。

"""本机密码认证，与企业签名身份令牌并行使用。"""
import hashlib
import secrets
import time
from sqlalchemy import delete
from .models import Credential, LoginSession, LoginThrottle, User


# 每次生成独立随机盐，用 scrypt 计算摘要，避免相同密码直接得到相同存储值。
def password_hash(password):
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode('utf-8'), salt=salt, n=16384, r=8, p=1)
    return f'scrypt${salt.hex()}${digest.hex()}'


# 按存储的算法与盐重新计算，并以固定时间比较摘要；格式异常视为校验失败。
def password_matches(password, encoded):
    try:
        # 盐与摘要都取自已存凭据，重算后用固定时间比较，避免普通字符串比较的时差。
        algorithm, salt, expected = encoded.split('$')
        if algorithm != 'scrypt': return False
        digest = hashlib.scrypt(password.encode('utf-8'), salt=bytes.fromhex(salt), n=16384, r=8, p=1)
        return secrets.compare_digest(digest.hex(), expected)
    except (ValueError, TypeError):
        return False


# 未知账号也执行同等密码计算，减少通过响应耗时判断账号是否存在的机会。
_DUMMY = password_hash(secrets.token_urlsafe(32))


# 在租户事务内维护账号/IP 分钟窗口；成功签发八小时会话，失败也要持久化尝试计数。
def issue_session(domain, tenant, account, password, address):
    from .domain import DomainError
    now = time.time()
    with domain.db.transaction(tenant) as s:
        s.execute(delete(LoginThrottle).where(LoginThrottle.tenant_id == tenant, LoginThrottle.expires_at <= now))
        s.execute(delete(LoginSession).where(LoginSession.tenant_id == tenant, LoginSession.expires_at <= now))
        windows = []
        # 账号和来源 IP 分别计数；换账号不能绕过 IP 限制，换 IP 也不能绕过账号限制。
        for key, limit in ((f'user:{account}', 5), (f'ip:{address}', 30)):
            id = hashlib.sha256(key.encode()).hexdigest()
            row = s.get(LoginThrottle, (tenant, id))
            if row is None:
                row = LoginThrottle(tenant_id=tenant, id=id, attempts=0, expires_at=now+60)
                s.add(row)
            windows.append((row, limit))
        limited = any(row.attempts >= limit for row, limit in windows)
        if not limited:
            # 验证前先累计尝试；即使账号不存在，也做同等密码计算，减少账号存在性泄露。
            for row, _ in windows: row.attempts += 1
            credential = s.get(Credential, (tenant, account))
            member = s.get(User, (tenant, account))
            valid = password_matches(password, credential.password_hash if credential else _DUMMY)
            if valid and member and member.active:
                # 成功仅清零账号计数，IP 窗口仍累计；令牌只返回明文，数据库仅保存摘要。
                windows[0][0].attempts = 0
                token = 'v3s_'+secrets.token_urlsafe(32)
                s.add(LoginSession(tenant_id=tenant, id=hashlib.sha256(token.encode()).hexdigest(),
                    owner=account, expires_at=now+8*3600))
                domain.audit(s, tenant, account, 'user.login', account)
                return {'access_token': token, 'token_type': 'bearer', 'expires_in': 8*3600}
    # 在事务提交后抛出登录失败，避免回滚尝试计数而使限速失效。
    if limited: raise DomainError('登录尝试过多，请 1 分钟后重试', 429)
    raise DomainError('账号或密码错误，或账号已停用', 401)


# 密码重置或成员停用时撤销该账号的全部本机登录会话，不影响其他成员。
def revoke_sessions(s, tenant, user):
    s.execute(delete(LoginSession).where(LoginSession.tenant_id == tenant, LoginSession.owner == user))
