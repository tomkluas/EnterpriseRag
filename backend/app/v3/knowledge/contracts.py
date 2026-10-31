from dataclasses import dataclass, asdict
from typing import Protocol


class BackendError(Exception):
    def __init__(self, code: str, message: str = '', *, reason: str = ''):
        self.code = code
        self.reason = reason
        super().__init__(message or code)


@dataclass(frozen=True)
class Binding:
    tenant_id: str
    document_id: str
    version_id: str
    generation_id: str
    instance_id: str
    collection_id: str
    remote_id: str
    id: str
    batch_id: str = ''


@dataclass(frozen=True)
class Scope:
    tenant_id: str
    bindings: tuple[Binding, ...]
    authz_revision: int = 0
    catalog_revision: int = 0
    route_revision: int = 0


@dataclass
class Evidence:
    tenant_id: str
    document_id: str
    version_id: str
    generation_id: str
    instance_id: str
    collection_id: str
    chunk_id: str
    content: str
    score: float
    locator_level: str
    locator: dict

    def to_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Submission:
    remote_id: str
    batch_id: str = ''


@dataclass(frozen=True)
class BackendStatus:
    state: str
    chunks: int
    raw_state: str


@dataclass(frozen=True)
class DeleteReceipt:
    accepted: bool
    confirmed: bool = False


class KnowledgeBackend(Protocol):
    async def prepare_collection(self, name: str) -> dict: ...
    async def submit(self, collection_id: str, filename: str, content: bytes, binding_id: str) -> Submission: ...
    async def reconcile_submission(self, collection_id: str, proof: dict) -> Submission: ...
    async def status(self, binding: Binding) -> BackendStatus: ...
    async def retrieve(self, query: str, scope: Scope, dispatch_gate=None) -> list[Evidence]: ...
    async def delete(self, binding: Binding) -> DeleteReceipt: ...
    async def deletion_confirmed(self, binding: Binding) -> bool: ...
    async def capabilities(self) -> dict: ...


def validate_scope(scope: Scope) -> dict[str, Binding]:
    result = {}
    groups = set()
    binding_ids = set()
    for binding in scope.bindings:
        if binding.tenant_id != scope.tenant_id or not all((binding.remote_id, binding.collection_id, binding.id)):
            raise BackendError('invalid_scope')
        if binding.remote_id in result or binding.id in binding_ids:
            raise BackendError('invalid_scope')
        groups.add((binding.instance_id, binding.collection_id))
        result[binding.remote_id] = binding
        binding_ids.add(binding.id)
    if len(groups) > 1:
        raise BackendError('invalid_scope', '检索批次必须属于同一底座实例和物理集合')
    return result
