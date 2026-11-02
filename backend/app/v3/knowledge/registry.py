# 底座工厂：按操作用途选择环境密钥引用，再构造对应供应商适配器。
import os
from .weknora import WeKnoraBackend
from .dify import DifyBackend
from .contracts import BackendError


class BackendRegistry:
    def __init__(self,client): self.client=client

    # 查询、写入、管理各取各自凭据；缺少所需密钥时直接失败，不借用其他用途密钥。
    def get(self,instance,purpose='retrieve'):
        # 真实密钥仅在创建适配器时从进程环境取出，不落入业务实例记录或 API 返回值。
        reference={'retrieve':instance.key_ref,'write':instance.write_key_ref,'manage':instance.manage_key_ref}[purpose]
        if not reference.startswith('V3_SECRET_') or not os.environ.get(reference):
            raise BackendError('invalid_configuration','底座所需服务凭据未配置')
        cls={'weknora':WeKnoraBackend,'dify':DifyBackend}.get(instance.provider)
        if not cls: raise BackendError('unsupported')
        return cls(instance.base_url,os.environ[reference],self.client,instance.collection_config)
