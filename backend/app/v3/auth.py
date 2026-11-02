# 请求身份入口：支持本机密码会话、开发启动令牌与企业签名令牌。
# 认证只确定账号；是否启用及可执行操作必须以当前数据库中的成员和角色为准。

import secrets
import hashlib
import time
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
import jwt


# 将环境配置与业务层封装为 FastAPI 的身份依赖，所有业务路由复用同一校验。
def authenticator(settings,domain,client):
    # 先验证令牌来源与有效期，再核对租户成员状态；不从请求参数接受新的租户边界。
    async def authenticate(request: Request):
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer '): raise HTTPException(401,'请登录企业账号')
        token=header[7:]
        if token.startswith('v3s_'):
            # 本机会话按令牌摘要查找，再检查到期与成员状态；重置密码可通过删会话立即撤销。
            from .models import LoginSession
            async with domain.db.atransaction(settings.tenant_id,shared=True) as session:
                login=session.get(LoginSession,(settings.tenant_id,hashlib.sha256(token.encode()).hexdigest()))
                if login is None or login.expires_at<=time.time(): raise HTTPException(401,'登录已失效，请重新登录')
                domain.user(session,settings.tenant_id,login.owner)
                return login.owner
        elif settings.mode=='development' and len(settings.bootstrap_token)>=32 and secrets.compare_digest(token,settings.bootstrap_token):
            user=settings.bootstrap_user
        else:
            if not all((settings.issuer,settings.audience,settings.jwks_url)) or urlsplit(settings.jwks_url).scheme!='https':
                raise HTTPException(401,'企业身份验证尚未配置')
            try:
                response=await client.get(settings.jwks_url,timeout=5,follow_redirects=False)
                response.raise_for_status()
                header=jwt.get_unverified_header(token)
                # 未验签头部仅用于挑公钥，算法固定为 RS256，不能由令牌自行选择。
                if header.get('alg')!='RS256': raise ValueError('algorithm')
                keys=[key for key in response.json().get('keys',[]) if key.get('kid')==header.get('kid') and key.get('kty')=='RSA' and key.get('use','sig')=='sig' and key.get('alg','RS256')=='RS256']
                if len(keys)!=1: raise ValueError('signing key')
                key=jwt.PyJWK.from_dict(keys[0]).key
                claims=jwt.decode(token,key,algorithms=['RS256'],audience=settings.audience,issuer=settings.issuer,
                    options={'require':['exp','sub','aud','iss']})
                if claims.get('tenant_id')!=settings.tenant_id: raise ValueError('tenant')
                # 签名、签发者、受众、到期和租户均通过后，才把 sub 当作本地成员标识。
                user=claims['sub']
            except Exception as error:
                # 上游身份验证失败只返回统一说明，不暴露令牌内容。
                raise HTTPException(401,'访问令牌无效或已过期') from error
        async with domain.db.atransaction(settings.tenant_id,shared=True) as session:
            # 企业签名有效也不能绕过本地停用；角色和权限仍由每个业务入口读取数据库。
            domain.user(session,settings.tenant_id,user)
        return user
    return authenticate
