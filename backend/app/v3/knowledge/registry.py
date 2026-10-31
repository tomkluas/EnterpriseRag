import os
from .weknora import WeKnoraBackend
from .dify import DifyBackend
from .contracts import BackendError


class BackendRegistry:
    def __init__(self,client): self.client=client

    def get(self,instance,purpose='retrieve'):
        reference={'retrieve':instance.key_ref,'write':instance.write_key_ref,'manage':instance.manage_key_ref}[purpose]
        if not reference.startswith('V3_SECRET_') or not os.environ.get(reference):
            raise BackendError('invalid_configuration','底座所需服务凭据未配置')
        cls={'weknora':WeKnoraBackend,'dify':DifyBackend}.get(instance.provider)
        if not cls: raise BackendError('unsupported')
        return cls(instance.base_url,os.environ[reference],self.client,instance.collection_config)
