import secrets
from urllib.parse import urlsplit
from fastapi import HTTPException, Request
import jwt


def authenticator(settings,domain,client):
    async def authenticate(request: Request):
        header=request.headers.get('authorization','')
        if not header.startswith('Bearer '): raise HTTPException(401,'请登录企业账号')
        token=header[7:]
        if settings.mode=='development' and len(settings.bootstrap_token)>=32 and secrets.compare_digest(token,settings.bootstrap_token):
            user=settings.bootstrap_user
        else:
            if not all((settings.issuer,settings.audience,settings.jwks_url)) or urlsplit(settings.jwks_url).scheme!='https':
                raise HTTPException(401,'企业身份验证尚未配置')
            try:
                response=await client.get(settings.jwks_url,timeout=5,follow_redirects=False)
                response.raise_for_status()
                header=jwt.get_unverified_header(token)
                if header.get('alg')!='RS256': raise ValueError('algorithm')
                keys=[key for key in response.json().get('keys',[]) if key.get('kid')==header.get('kid') and key.get('kty')=='RSA' and key.get('use','sig')=='sig' and key.get('alg','RS256')=='RS256']
                if len(keys)!=1: raise ValueError('signing key')
                key=jwt.PyJWK.from_dict(keys[0]).key
                claims=jwt.decode(token,key,algorithms=['RS256'],audience=settings.audience,issuer=settings.issuer,
                    options={'require':['exp','sub','aud','iss']})
                if claims.get('tenant_id')!=settings.tenant_id: raise ValueError('tenant')
                user=claims['sub']
            except Exception as error:
                # Upstream identity errors never expose token contents.
                raise HTTPException(401,'访问令牌无效或已过期') from error
        async with domain.db.atransaction(settings.tenant_id,shared=True) as session:
            domain.user(session,settings.tenant_id,user)
        return user
    return authenticate
