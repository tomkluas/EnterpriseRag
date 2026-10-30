from abc import ABC, abstractmethod


class SearchResult:
    def __init__(self, title: str, url: str, content: str, score: float = 0.5):
        self.title = title
        self.url = url
        self.content = content
        self.score = score


class BaseSearchProvider(ABC):
    @abstractmethod
    async def search(self, query: str, max_results: int | None = None) -> list[SearchResult]:
        """执行搜索，返回结果列表"""
        ...
