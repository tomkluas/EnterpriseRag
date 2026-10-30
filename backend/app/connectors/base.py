from abc import ABC, abstractmethod
from typing import Any


class BaseConnector(ABC):
    """数据库连接器抽象基类"""

    @abstractmethod
    def connect(self) -> Any:
        """建立连接"""
        pass

    @abstractmethod
    def execute(self, query: str) -> list[dict]:
        """执行查询，返回结果列表"""
        pass

    @abstractmethod
    def get_schema(self) -> dict:
        """获取数据库Schema信息"""
        pass

    @abstractmethod
    def close(self) -> None:
        """关闭连接"""
        pass
