# 不可变原件存储：统一本地目录与 S3 的读写，供版本摄取、下载和恢复核验使用。
import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from botocore.config import Config
import boto3


# S3 使用独立对象存储凭据，本地模式提供同一对象键接口。
class OriginalStore:
    def __init__(self,location):
        # 按 URI 选择存储实现，S3 凭据单独读取，不能借用知识底座或模型服务的密钥。
        self.location=str(location)
        self.client=None
        if self.location.startswith('s3://'):
            url=urlsplit(self.location); self.bucket=url.netloc; self.prefix=url.path.strip('/')
            if not self.bucket or url.query or url.fragment: raise ValueError('对象存储 URI 无效')
            access=os.getenv('V3_SECRET_S3_ACCESS');secret=os.getenv('V3_SECRET_S3_SECRET')
            if not access or not secret: raise ValueError('请配置独立的 V3 对象存储凭据')
            self.client=boto3.client('s3',endpoint_url=os.getenv('V3_S3_ENDPOINT') or None,
                region_name=os.getenv('V3_S3_REGION','us-east-1'),aws_access_key_id=access,aws_secret_access_key=secret,
                config=Config(connect_timeout=5,read_timeout=30,retries={'max_attempts':3},s3={'addressing_style':'path'}))
        else:
            self.root=Path(location).resolve();self.root.mkdir(parents=True,exist_ok=True)

    # 仅接受相对对象键，禁止上级目录、反斜杠和盘符，防止逃逸出原件目录。
    def key(self,key):
        if not key or '..' in key.split('/') or key.startswith(('/','\\')) or '\\' in key or ':' in key:
            raise ValueError('无效对象键')
        return '/'.join(filter(None,(self.prefix,key))) if self.client else key

    # 只创建不覆盖：S3 条件创建、本地独占创建，保证已有版本原件不能被同键替换。
    def put(self,key,content):
        key=self.key(key)
        if self.client:
            self.client.put_object(Bucket=self.bucket,Key=key,Body=content,IfNoneMatch='*',Metadata={'sha256':hashlib.sha256(content).hexdigest()})
        else:
            # xb 使用文件系统独占创建；同键已存在就失败，不能覆盖历史版本原件。
            target=self.root/key;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(content)

    # S3 响应体读取后显式关闭，避免占用连接；业务摘要核验由调用方负责。
    def read(self,key):
        key=self.key(key)
        if self.client:
            body=self.client.get_object(Bucket=self.bucket,Key=key)['Body']
            # 即使读取异常也关闭响应体，避免长时间运行的 Worker 耗尽对象存储连接池。
            try:return body.read()
            finally:body.close()
        return (self.root/key).read_bytes()
