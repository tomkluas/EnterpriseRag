# 数据库与事务边界：正式运行使用 PostgreSQL，SQLite 仅服务隔离自动化测试。
# 租户级协调锁让读取权限快照与权限/路由变更有一致边界；任务领取另用行锁避免重复执行。

from contextlib import contextmanager, asynccontextmanager
import asyncio
from threading import RLock
import hashlib
from sqlalchemy import create_engine, text, inspect
from sqlalchemy.orm import sessionmaker
from .models import Base


# 持有连接池与会话工厂；迁移必须显式调用，不在 API 启动时隐式修改业务库。
class Database:
    # 建立连接与会话工厂；测试环境允许跨线程使用临时 SQLite。
    def __init__(self, url: str, testing=False):
        if not testing and not url.startswith(('postgresql://','postgresql+psycopg2://')):
            raise ValueError('V3 正式运行必须使用独立 PostgreSQL；SQLite 仅用于自动化测试')
        kwargs = {'connect_args': {'check_same_thread':False}} if url.startswith('sqlite:') else {}
        self.engine = create_engine(url, pool_pre_ping=True, **kwargs)
        self.sessions = sessionmaker(self.engine, expire_on_commit=False)
        self.testing = testing
        self._local_gate = RLock()

    # 在一个连接事务中升级结构、迁移角色授权并更新备注；提交前校验失败可回滚。
    def migrate(self,validate=None):
        # 仅由显式数据库迁移命令调用，API 启动时不自动执行。
        with self.engine.begin() as connection:
            # 迁移使用固定事务锁，防止两个维护进程同时检查并新增同一字段。
            if self.engine.dialect.name=='postgresql':
                connection.execute(text('SELECT pg_advisory_xact_lock(73003001)'))
            Base.metadata.create_all(connection)
            columns={column['name'] for column in inspect(connection).get_columns('v3_backends')}
            if 'collection_config' not in columns:
                connection.execute(text("ALTER TABLE v3_backends ADD COLUMN collection_config JSON NOT NULL DEFAULT '{}'"))
            if 'dependencies' not in {column['name'] for column in inspect(connection).get_columns('v3_messages')}:
                connection.execute(text("ALTER TABLE v3_messages ADD COLUMN dependencies JSON NOT NULL DEFAULT '[]'"))
            if 'created_at' not in {column['name'] for column in inspect(connection).get_columns('v3_jobs')}:
                # 不能根据重试调度时间推算历史创建时间，旧记录保留未知值。
                connection.execute(text('ALTER TABLE v3_jobs ADD COLUMN created_at FLOAT NOT NULL DEFAULT 0'))
            if 'trace' not in {column['name'] for column in inspect(connection).get_columns('v3_messages')}:
                connection.execute(text("ALTER TABLE v3_messages ADD COLUMN trace JSON NOT NULL DEFAULT '{}'"))
            additions={
                'v3_tenants':{'name':"VARCHAR(180) NOT NULL DEFAULT '企业'",'active':'BOOLEAN NOT NULL DEFAULT TRUE','access_version':'INTEGER NOT NULL DEFAULT 0'},
                'v3_users':{'display_name':"VARCHAR(128) NOT NULL DEFAULT ''",'org_unit_id':"VARCHAR(64) NOT NULL DEFAULT ''"},
                'v3_documents':{'inherit_permissions':'BOOLEAN NOT NULL DEFAULT TRUE'},
                'v3_conversations':{'archived':'BOOLEAN NOT NULL DEFAULT FALSE'},
                'v3_messages':{'space_id':"VARCHAR(64) NOT NULL DEFAULT ''"},
            }
            for table,fields in additions.items():
                present={column['name'] for column in inspect(connection).get_columns(table)}
                for name,definition in fields.items():
                    if name not in present:connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {name} {definition}'))
            if self.engine.dialect.name=='postgresql':
                connection.execute(text('ALTER TABLE v3_audit ALTER COLUMN resource_id TYPE VARCHAR(128)'))
            from sqlalchemy.orm import Session
            from .access import migrate_tenant_access
            from .models import Tenant
            from sqlalchemy import select
            with Session(bind=connection) as session:
                # 角色迁移复用当前连接，确保结构更新与旧授权数据转换一起提交或回滚。
                for tenant in session.scalars(select(Tenant)):
                    migrate_tenant_access(session,tenant.id)
                session.flush()
            for table in Base.metadata.sorted_tables:
                for index in table.indexes:index.create(connection,checkfirst=True)
            if self.engine.dialect.name == 'postgresql':
                from sqlalchemy.schema import SetTableComment, SetColumnComment
                for table in Base.metadata.sorted_tables:
                    connection.execute(SetTableComment(table))
                    for column in table.columns:
                        connection.execute(SetColumnComment(column))
            connection.execute(text('CREATE TABLE IF NOT EXISTS v3_schema_versions (version INTEGER PRIMARY KEY)'))
            if self.engine.dialect.name == 'postgresql':
                connection.execute(text("COMMENT ON TABLE v3_schema_versions IS '已执行的业务数据库版本'"))
                connection.execute(text("COMMENT ON COLUMN v3_schema_versions.version IS '数据库版本号'"))
            if not connection.scalar(text('SELECT version FROM v3_schema_versions WHERE version=1')):
                connection.execute(text('INSERT INTO v3_schema_versions (version) VALUES (1)'))
            if not connection.scalar(text('SELECT version FROM v3_schema_versions WHERE version=2')):
                connection.execute(text('INSERT INTO v3_schema_versions (version) VALUES (2)'))
            if not connection.scalar(text('SELECT version FROM v3_schema_versions WHERE version=3')):
                connection.execute(text('INSERT INTO v3_schema_versions (version) VALUES (3)'))
            if validate is not None:
                # 校验仍在外层事务内，校验失败不能留下已提交的半成品迁移。
                with Session(bind=connection) as session:validate(session)

    @contextmanager
    # 提供同步事务；shared 用于协调中的读路径，写路径使用租户独占协调锁。
    def transaction(self, tenant_id=None, shared=False):
        with self._local_gate if self.testing else _Noop():
            with self.sessions.begin() as session:
                if tenant_id and self.engine.dialect.name == 'postgresql':
                    session.execute(text("SET LOCAL lock_timeout = '5s'"))
                    key = int.from_bytes(hashlib.sha256(tenant_id.encode()).digest()[:8], 'big', signed=True)
                    # 相同租户得到稳定锁键；读路径可共享，权限/路由写入必须等全部共享锁释放。
                    function = 'pg_advisory_xact_lock_shared' if shared else 'pg_advisory_xact_lock'
                    session.execute(text(f'SELECT {function}(:key)'), {'key':key})
                yield session

    @asynccontextmanager
    # 异步调用同步事务：把可能阻塞的进入/退出放到线程执行，并处理取消时的连接释放。
    async def atransaction(self,tenant_id=None,shared=False):
        if self.testing:
            with self.transaction(tenant_id,shared) as session:
                yield session
            return
        manager=self.transaction(tenant_id,shared)
        entering=asyncio.create_task(asyncio.to_thread(manager.__enter__))
        # 取消协程无法停止已进入线程的数据库操作，先保护进入任务，再负责等它结束并退出。
        try:
            session=await asyncio.shield(entering)
        except asyncio.CancelledError as error:
            # 进入事务时被取消也要回收可能已经取得的连接，并按异常退出触发回滚。
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
            # 把业务异常原样传给同步事务，取消也按异常处理，不能误提交未完成业务。
            await asyncio.to_thread(manager.__exit__,type(error),error,error.__traceback__)
            raise
        else:
            await asyncio.to_thread(manager.__exit__,None,None,None)


# 正式数据库不使用进程内测试锁；保留相同上下文管理接口。
class _Noop:
    def __enter__(self): return self
    def __exit__(self,*args): return False
