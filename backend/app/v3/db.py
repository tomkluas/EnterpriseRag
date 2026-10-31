from contextlib import contextmanager, asynccontextmanager
import asyncio
from threading import RLock
import hashlib
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from .models import Base


class Database:
    def __init__(self, url: str, testing=False):
        if not testing and not url.startswith(('postgresql://','postgresql+psycopg2://')):
            raise ValueError('V3 正式运行必须使用独立 PostgreSQL；SQLite 仅用于自动化测试')
        kwargs = {'connect_args': {'check_same_thread':False}} if url.startswith('sqlite:') else {}
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.testing = testing
        self._local_gate = RLock()

    def migrate(self):
        # Only the explicit CLI migration step calls this, never API startup.
        with self.engine.begin() as connection:
            if self.engine.dialect.name=='postgresql':
                connection.execute(text('SELECT pg_advisory_xact_lock(73003001)'))
            Base.metadata.create_all(connection)
            columns={column['name'] for column in inspect(connection).get_columns('v3_backends')}
            if 'collection_config' not in columns:
                connection.execute(text("ALTER TABLE v3_backends ADD COLUMN collection_config JSON NOT NULL DEFAULT '{}'"))
            if 'dependencies' not in {column['name'] for column in inspect(connection).get_columns('v3_messages')}:
                connection.execute(text("ALTER TABLE v3_messages ADD COLUMN dependencies JSON NOT NULL DEFAULT '[]'"))
            if 'created_at' not in {column['name'] for column in inspect(connection).get_columns('v3_jobs')}:
                # Historic creation times cannot be reconstructed from a retry schedule.
                connection.execute(text('ALTER TABLE v3_jobs ADD COLUMN created_at FLOAT NOT NULL DEFAULT 0'))
            connection.execute(text('CREATE TABLE IF NOT EXISTS v3_schema_versions (version INTEGER PRIMARY KEY)'))
            if not connection.scalar(text('SELECT version FROM v3_schema_versions WHERE version=1')):
                connection.execute(text('INSERT INTO v3_schema_versions (version) VALUES (1)'))

    @contextmanager
    def transaction(self, tenant_id=None, shared=False):
        with self._local_gate if self.testing else _Noop():
            with self.sessions.begin() as session:
                if tenant_id and self.engine.dialect.name == 'postgresql':
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    key = int.from_bytes(hashlib.sha256(tenant_id.encode()).digest()[:8], 'big', signed=True)
                    function = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
                    session.execute(text(f'SELECT {function}(:key)'), {'key':key})
                yield session

    @asynccontextmanager
    async def atransaction(self,tenant_id=None,shared=False):
        if self.testing:
            with self.transaction(tenant_id,shared) as session:
                yield session
            return
        manager=self.transaction(tenant_id,shared)
        entering=asyncio.create_task(asyncio.to_thread(manager.__enter__))
        try:
            session=await asyncio.shield(entering)
        except asyncio.CancelledError as error:
            try:
                await entering
            except Exception:
                pass
            else:
                await asyncio.to_thread(manager.__exit__,type(error),error,error.__traceback__)
            raise
        try:
            yield session
        except BaseException as error:
            await asyncio.to_thread(manager.__exit__,type(error),error,error.__traceback__)
            raise
        else:
            await asyncio.to_thread(manager.__exit__,None,None,None)


class _Noop:
    def __enter__(self): return self
    def __exit__(self,*args): return False
