import hashlib
import os
from pathlib import Path
from urllib.parse import urlsplit
from botocore.config import Config
import boto3


class OriginalStore:
    def __init__(self,location):
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

    def key(self,key):
        if not key or '..' in key.split('/') or key.startswith(('/','\\')) or '\\' in key or ':' in key:
            raise ValueError('无效对象键')
        return '/'.join(filter(None,(self.prefix,key))) if self.client else key

    def put(self,key,content):
        key=self.key(key)
        if self.client:
            self.client.put_object(Bucket=self.bucket,Key=key,Body=content,IfNoneMatch='*',Metadata={'sha256':hashlib.sha256(content).hexdigest()})
        else:
            target=self.root/key;target.parent.mkdir(parents=True,exist_ok=True)
            with target.open('xb') as stream:stream.write(content)

    def read(self,key):
        key=self.key(key)
        if self.client:
            body=self.client.get_object(Bucket=self.bucket,Key=key)['Body']
            try:return body.read()
            finally:body.close()
        return (self.root/key).read_bytes()
