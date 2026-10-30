from abc import ABC, abstractmethod


class BaseConnector(ABC):
    @abstractmethod
    def test_connection(self) -> bool:
        """测试连接可用性"""

    @abstractmethod
    def get_schema(self) -> dict:
        """内省并返回表结构"""

    @abstractmethod
    def query(self, nl_query: str, schema: dict, llm) -> list[dict]:
        """自然语言转查询并执行，返回结果行列表"""
