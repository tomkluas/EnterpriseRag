import time
from sqlalchemy import String, Text, Boolean, Integer, Float, JSON, UniqueConstraint, Index
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase): pass


class Tenant(Base):
    __tablename__ = 'v3_tenants'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    authz: Mapped[int] = mapped_column(Integer,default=0)
    catalog: Mapped[int] = mapped_column(Integer,default=0)


class User(Base):
    __tablename__ = 'v3_users'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    id: Mapped[str] = mapped_column(String(128),primary_key=True)
    role: Mapped[str] = mapped_column(String(24),default='reader')
    active: Mapped[bool] = mapped_column(Boolean,default=True)
    groups: Mapped[list] = mapped_column(JSON,default=list)


class Space(Base):
    __tablename__ = 'v3_spaces'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    owner: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(180))
    readers: Mapped[list] = mapped_column(JSON,default=list)
    denied: Mapped[list] = mapped_column(JSON,default=list)
    required: Mapped[list] = mapped_column(JSON,default=lambda:['segment'])
    active_backend: Mapped[str] = mapped_column(String(64),default='')
    route_revision: Mapped[int] = mapped_column(Integer,default=0)


class Document(Base):
    __tablename__ = 'v3_documents'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    space_id: Mapped[str] = mapped_column(String(64),index=True)
    owner: Mapped[str] = mapped_column(String(128))
    name: Mapped[str] = mapped_column(String(240))
    readers: Mapped[list] = mapped_column(JSON,default=list)
    denied: Mapped[list] = mapped_column(JSON,default=list)
    withdrawn: Mapped[bool] = mapped_column(Boolean,default=False)
    current_version: Mapped[str] = mapped_column(String(64),default='')


class Version(Base):
    __tablename__ = 'v3_document_versions'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    document_id: Mapped[str] = mapped_column(String(64),index=True)
    sha256: Mapped[str] = mapped_column(String(64))
    object_key: Mapped[str] = mapped_column(String(240))
    filename: Mapped[str] = mapped_column(String(240))
    state: Mapped[str] = mapped_column(String(32),default='received')
    created_at: Mapped[float] = mapped_column(Float,default=time.time)


class BackendInstance(Base):
    __tablename__ = 'v3_backends'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    provider: Mapped[str] = mapped_column(String(24))
    base_url: Mapped[str] = mapped_column(String(600))
    key_ref: Mapped[str] = mapped_column(String(128))
    write_key_ref: Mapped[str] = mapped_column(String(128),default='')
    manage_key_ref: Mapped[str] = mapped_column(String(128),default='')
    collection_config: Mapped[dict] = mapped_column(JSON,default=dict)
    verified: Mapped[bool] = mapped_column(Boolean,default=False)
    capabilities: Mapped[list] = mapped_column(JSON,default=list)
    report: Mapped[dict] = mapped_column(JSON,default=dict)


class Replica(Base):
    __tablename__ = 'v3_backend_bindings'
    __table_args__ = (UniqueConstraint('tenant_id','version_id','instance_id'),
        UniqueConstraint('instance_id','collection_id','remote_id'))
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    version_id: Mapped[str] = mapped_column(String(64),index=True)
    instance_id: Mapped[str] = mapped_column(String(64),index=True)
    collection_id: Mapped[str | None] = mapped_column(String(128),nullable=True)
    remote_id: Mapped[str | None] = mapped_column(String(128),nullable=True)
    batch_id: Mapped[str] = mapped_column(String(180),default='')
    metadata_id: Mapped[str] = mapped_column(String(128),default='')
    state: Mapped[str] = mapped_column(String(32),default='received')
    chunks: Mapped[int] = mapped_column(Integer,default=0)
    error: Mapped[str] = mapped_column(Text,default='')


class Job(Base):
    __tablename__ = 'v3_jobs'
    __table_args__ = (UniqueConstraint('tenant_id','idempotency_key'), Index('ix_v3_jobs_due','state','next_run'))
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    owner: Mapped[str] = mapped_column(String(128))
    kind: Mapped[str] = mapped_column(String(24))
    idempotency_key: Mapped[str] = mapped_column(String(240))
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(32),default='queued')
    created_at: Mapped[float] = mapped_column(Float,default=time.time)
    next_run: Mapped[float] = mapped_column(Float,default=0)
    lease_token: Mapped[str] = mapped_column(String(64),default='')
    lease_until: Mapped[float] = mapped_column(Float,default=0)
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    failures: Mapped[int] = mapped_column(Integer,default=0)
    error: Mapped[str] = mapped_column(Text,default='')


class BackendSwitch(Base):
    __tablename__ = 'v3_backend_switches'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    space_id: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64))
    authz_revision: Mapped[int] = mapped_column(Integer)
    catalog_revision: Mapped[int] = mapped_column(Integer)
    route_revision: Mapped[int] = mapped_column(Integer)
    manifest: Mapped[list] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String(24),default='preparing')


class Conversation(Base):
    __tablename__ = 'v3_conversations'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    owner: Mapped[str] = mapped_column(String(128))
    title: Mapped[str] = mapped_column(String(180),default='新会话')


class Message(Base):
    __tablename__ = 'v3_messages'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    conversation_id: Mapped[str] = mapped_column(String(64),index=True)
    owner: Mapped[str] = mapped_column(String(128))
    query: Mapped[str] = mapped_column(Text)
    answer: Mapped[str] = mapped_column(Text,default='')
    state: Mapped[str] = mapped_column(String(24),default='processing')
    evidence: Mapped[list] = mapped_column(JSON,default=list)
    dependencies: Mapped[list] = mapped_column(JSON,default=list)
    feedback: Mapped[str] = mapped_column(Text,default='')
    created_at: Mapped[float] = mapped_column(Float,default=time.time)


class Audit(Base):
    __tablename__ = 'v3_audit'
    id: Mapped[str] = mapped_column(String(64),primary_key=True)
    tenant_id: Mapped[str] = mapped_column(String(64),index=True)
    actor: Mapped[str] = mapped_column(String(128))
    action: Mapped[str] = mapped_column(String(64))
    resource_id: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[float] = mapped_column(Float,default=time.time)


class WorkerPulse(Base):
    __tablename__ = 'v3_worker_pulses'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    worker_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    last_seen: Mapped[float] = mapped_column(Float)
    started_at: Mapped[float] = mapped_column(Float)


class MetricBucket(Base):
    __tablename__ = 'v3_metric_buckets'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    stage: Mapped[str] = mapped_column(String(32),primary_key=True)
    outcome: Mapped[str] = mapped_column(String(16),primary_key=True)
    bucket: Mapped[int] = mapped_column(Integer,primary_key=True)
    count: Mapped[int] = mapped_column(Integer,default=0)
    total_seconds: Mapped[float] = mapped_column(Float,default=0)
    max_seconds: Mapped[float] = mapped_column(Float,default=0)


class AlertState(Base):
    __tablename__ = 'v3_alert_states'
    tenant_id: Mapped[str] = mapped_column(String(64),primary_key=True)
    code: Mapped[str] = mapped_column(String(64),primary_key=True)
    active: Mapped[bool] = mapped_column(Boolean,default=True)
    event_id: Mapped[str] = mapped_column(String(64))
    changed_at: Mapped[float] = mapped_column(Float)
    delivery: Mapped[str] = mapped_column(String(16),default='disabled')
    attempts: Mapped[int] = mapped_column(Integer,default=0)
    next_attempt: Mapped[float] = mapped_column(Float,default=0)
    lease_until: Mapped[float] = mapped_column(Float,default=0)
    delivered_at: Mapped[float] = mapped_column(Float,default=0)
    error: Mapped[str] = mapped_column(String(32),default='')
