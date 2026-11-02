# 离线逻辑备份与恢复：业务记录和自有原件共同校验，不用于覆盖正在服务的目标库。
# 目标必须为空；远端写入结果不明时保留恢复日志，不能以失败为由自动删除可能不属于自己的对象。

"""离线执行租户逻辑恢复，不得向正在提供业务服务的目标执行恢复。"""
import csv
import hashlib
import io
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import subprocess
import uuid
from urllib.parse import urlsplit

from sqlalchemy import select
from .models import Base, Tenant, Version, Job, BackendInstance, Message, BackendSwitch
from .storage import OriginalStore


TABLES = {table.name: table for table in Base.metadata.sorted_tables}
RUNTIME_TABLES = ('v3_worker_pulses', 'v3_metric_buckets', 'v3_alert_states', 'v3_login_sessions', 'v3_login_throttles')


# 计算清单和原件的完整性摘要，发现不一致时在恢复写入前拒绝。
def _digest(data):
    return hashlib.sha256(data).hexdigest()


# 固定 JSON 的键顺序与分隔符，使同一份业务记录的备份摘要可复验。
def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


# 创建新的私有目录；Windows 额外移除继承权限并仅授予当前用户访问。
def _private_directory(path):
    # 目录必须新建；Windows 的 mode 不足以限制 ACL，因此再移除继承并授予当前用户权限。
    path.mkdir(mode=0o700)
    if os.name == 'nt':
        try:
            result = subprocess.check_output(['whoami', '/user', '/fo', 'csv', '/nh'], text=True)
            sid = next(csv.reader(io.StringIO(result)))[1]
            subprocess.run(['icacls', str(path), '/inheritance:r', '/grant:r', f'*{sid}:(OI)(CI)F'],
                           check=True, capture_output=True)
        except BaseException:
            path.rmdir()
            raise


# 拒绝绝对路径、路径回退、盘符与反斜线，保持对象键在预期命名空间内。
def _safe_key(key):
    if not isinstance(key, str) or not key or '\\' in key or ':' in key:
        raise ValueError('Invalid object key')
    path = PurePosixPath(key)
    if path.is_absolute() or any(p in ('', '.', '..') for p in key.split('/')):
        raise ValueError('Invalid object key')
    return key


# 读取备份文件前检查真实路径和符号链接，避免清单把读取范围引到备份根外。
def _read_file(root, relative):
    _safe_key(relative)
    # 同时限制键的文本形式与最终落点，符号链接即使指回根目录也拒绝，避免清单改变读取边界。
    path = root / relative
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('Symlink in recovery bundle')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Recovery path escaped bundle')
    return path.read_bytes()


# 只接受明确指定的空 S3 前缀，并拒绝有版本控制的恢复目标。
def _empty_s3_destination(location):
    """恢复目标必须是未启用版本控制的空命名空间，无法确认时直接拒绝。"""
    uri = urlsplit(location)
    prefix = uri.path[1:].rstrip('/')
    if not prefix or '@' in uri.netloc:
        raise ValueError('Recovery requires an explicit private S3 prefix')
    _safe_key(prefix)
    store = OriginalStore(location)
    versioning = store.client.get_bucket_versioning(Bucket=store.bucket)
    # 版本化桶即使当前列表为空也可能有旧版本；本恢复流程只支持无版本且空的独立前缀。
    if versioning.get('Status'):
        raise ValueError('S3 versioned buckets are not supported by logical recovery')
    pages = store.client.get_paginator('list_objects_v2').paginate(
        Bucket=store.bucket, Prefix=store.prefix + '/')
    for page in pages:
        if page.get('Contents'):
            raise ValueError('S3 recovery prefix must be empty')
    return store


# 写入临时文件、刷盘并替换清单；远端操作前后都留下可恢复的阶段记录。
def _write_journal(directory, journal):
    """写入前先持久化日志；崩溃可能留下未知结果，不能据此自动删除对象。"""
    temporary = directory / 'manifest.tmp'
    with temporary.open('wb') as stream:
        stream.write(_encode(journal))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / 'manifest.json')


# 先记录写入结果未知，再上传、回读和验摘要，不把请求返回等同于原件已验证。
def _upload_recovery_objects(store, contents, journal_path, journal):
    for entry in journal['objects']:
        key = entry['key']
        entry['state'] = 'write_unknown'
        # 写前刷盘：进程可能在服务端接收后崩溃，此时日志必须保守地保留未知结果。
        _write_journal(journal_path, journal)
        store.put(key, contents[key])
        entry['state'] = 'written'
        _write_journal(journal_path, journal)
        if _digest(store.read(key)) != entry['sha256']:
            raise ValueError('Restored original checksum mismatch')
        entry['state'] = 'verified'
        _write_journal(journal_path, journal)


# 持有租户协调锁导出业务记录，并按版本清单复制和核对原件，保证备份内容相互一致。
def backup_tenant(db, originals: OriginalStore, tenant_id: str, destination) -> dict:
    """创建新的私有备份目录，并在整个备份过程中持有租户协调锁。"""
    destination = Path(destination).absolute()
    _private_directory(destination)
    try:
        with db.transaction(tenant_id) as session:
            if session.get(Tenant, tenant_id) is None:
                raise ValueError('Tenant does not exist')
            rows = {}
            for name, table in TABLES.items():
                predicate = table.c.id == tenant_id if table.name == Tenant.__tablename__ else table.c.tenant_id == tenant_id
                rows[name] = [dict(row) for row in session.execute(select(table).where(predicate)).mappings()]
            (destination / 'objects').mkdir(mode=0o700)
            objects = {}
            # 版本可共享原件键，只复制一次；同键若声明不同摘要，先拒绝备份而非任选其一。
            for version in rows[Version.__tablename__]:
                key = _safe_key(version['object_key'])
                if key in objects:
                    if objects[key]['sha256'] != version['sha256']:
                        raise ValueError('Conflicting original hashes')
                    continue
                # 本地符号链接不能让备份读取配置的原件根目录之外的文件。
                if not originals.client:
                    _read_file(originals.root, key)
                data = originals.read(key)
                if _digest(data) != version['sha256']:
                    raise ValueError('Original checksum mismatch')
                relative = f'objects/{len(objects)}'
                (destination / relative).write_bytes(data)
                objects[key] = {'file': relative, 'sha256': _digest(data)}
            data = _encode(rows)
            # 原件全部校验通过后再生成记录摘要和清单，恢复时可检查两者是否来自同一份备份。
            (destination / 'rows.json').write_bytes(data)
            manifest = {'format': 1, 'tenant_id': tenant_id, 'rows_sha256': _digest(data), 'objects': objects}
            (destination / 'manifest.json').write_bytes(_encode(manifest))
            return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


# 先验证清单、租户和所有原件，再向空目标写入；恢复后撤销运行态并要求重新验收底座。
def restore_tenant(db, tenant_id: str, bundle, original_destination) -> dict:
    """离线恢复到空租户，原件目标只能是新建本地目录或空 S3 前缀。"""
    bundle = Path(bundle).absolute()
    manifest = json.loads(_read_file(bundle, 'manifest.json'))
    if manifest.get('format') != 1 or manifest.get('tenant_id') != tenant_id:
        raise ValueError('Bundle format or tenant mismatch')
    raw = _read_file(bundle, 'rows.json')
    if _digest(raw) != manifest['rows_sha256']:
        raise ValueError('Database checksum mismatch')
    rows = json.loads(raw)
    # 仅补充明确支持的新增字段/表；其他结构差异仍报错，不能静默丢弃未知业务数据。
    for name in RUNTIME_TABLES:
        if name in TABLES:
            rows.setdefault(name, [])
    rows.setdefault('v3_credentials', [])
    for name in ('v3_roles','v3_user_roles','v3_organization_units'):rows.setdefault(name,[])
    for row in rows.get('v3_tenants',[]):
        row.setdefault('name','企业');row.setdefault('active',True);row.setdefault('access_version',0)
    for row in rows.get('v3_users',[]):
        row.setdefault('display_name','');row.setdefault('org_unit_id','')
    for row in rows.get('v3_documents',[]):row.setdefault('inherit_permissions',True)
    for row in rows.get(Message.__tablename__, []):
        row.setdefault('trace', {})
    for row in rows.get(Job.__tablename__, []):
        if 'created_at' in TABLES[Job.__tablename__].c:
            row.setdefault('created_at', 0)
    if set(rows) != set(TABLES):
        raise ValueError('Schema mismatch')
    for name, table in TABLES.items():
        for row in rows[name]:
            if set(row) != set(table.c.keys()) or row.get('id' if name == Tenant.__tablename__ else 'tenant_id') != tenant_id:
                raise ValueError('Foreign tenant or schema mismatch')
    if len(rows[Tenant.__tablename__]) != 1:
        raise ValueError('Missing tenant')
    expected = {}
    # 原件键必须属于待恢复租户；版本引用和清单对象集合需完全对应，防止遗漏或夹带对象。
    for version in rows[Version.__tablename__]:
        key = _safe_key(version['object_key'])
        if not key.startswith((tenant_id + '/', hashlib.sha256(tenant_id.encode()).hexdigest() + '/')):
            raise ValueError('Original key is outside tenant namespace')
        if key in expected and expected[key] != version['sha256']:
            raise ValueError('Conflicting original hashes')
        expected[key] = version['sha256']
    if set(expected) != set(manifest['objects']):
        raise ValueError('Original inventory mismatch')
    contents = {}
    # 在创建目标或写业务库前读完并校验全部原件，坏备份不能留下部分恢复的数据。
    for key, checksum in expected.items():
        entry = manifest['objects'][key]
        content = _read_file(bundle, entry['file'])
        if _digest(content) != checksum or entry['sha256'] != checksum:
            raise ValueError('Original checksum mismatch')
        contents[key] = content
    s3 = str(original_destination).startswith('s3://')
    destination = None if s3 else Path(original_destination).absolute()
    created = False
    journal_path = None
    journal = None
    committed = False
    try:
        with db.transaction(tenant_id) as session:
            for name, table in TABLES.items():
                predicate = table.c.id == tenant_id if name == Tenant.__tablename__ else table.c.tenant_id == tenant_id
                if session.execute(select(table).where(predicate).limit(1)).first():
                    raise ValueError('Target tenant is not empty')
            if s3:
                # S3 写入不参与数据库回滚，先建立独立恢复日志，失败也保留现场供核查。
                store = _empty_s3_destination(str(original_destination))
                journal_path = bundle.parent / ('recovery-attempt-' + uuid.uuid4().hex)
                _private_directory(journal_path)
                journal = {'format': 1, 'tenant_id': tenant_id, 'destination': str(original_destination),
                           'state': 'in_progress', 'automatic_delete': False,
                           'objects': [{'key': key, 'sha256': _digest(content), 'state': 'planned'}
                                       for key, content in contents.items()]}
                _write_journal(journal_path, journal)
                _upload_recovery_objects(store, contents, journal_path, journal)
            else:
                _private_directory(destination)
                created = True
                store = OriginalStore(destination)
                for key, content in contents.items():
                    store.put(key, content)
            # 恢复前的运行记录不能作为新环境的健康依据。
            for name in RUNTIME_TABLES:
                # 旧心跳、监控与会话不能在新环境复活，恢复后要重新建立运行状态。
                if name in rows:
                    rows[name] = []
            for row in rows[Job.__tablename__]:
                # 清掉旧租约；未终结任务统一等待对账，避免恢复后自动重放未知的远端写入。
                row['lease_token'] = ''; row['lease_until'] = 0
                if row['state'] not in ('completed', 'cancelled', 'failed'):
                    row['state'] = 'needs_reconciliation'
            for row in rows[BackendInstance.__tablename__]:
                # 备份环境的验收不能代表恢复环境可用，清空报告并要求重新验收。
                row['verified'] = False
                row['report'] = {}
            for row in rows[Message.__tablename__]:
                if row['state'] == 'processing':
                    row['state'] = 'failed'
            for row in rows[BackendSwitch.__tablename__]:
                row['state'] = 'invalidated'
            for name, table in TABLES.items():
                if rows[name]:
                    # TABLES 来自依赖排序，按外键依赖顺序插入，所有记录随当前事务一起提交。
                    session.execute(table.insert(), rows[name])
            from .access import migrate_tenant_access
            migrate_tenant_access(session,tenant_id)
        committed = True
        result = {'tenant_id': tenant_id, 'rows': sum(map(len, rows.values())), 'objects': len(contents)}
        if journal is not None:
            journal['state'] = 'committed'
            _write_journal(journal_path, journal)
            result['recovery_manifest'] = str(journal_path / 'manifest.json')
        return result
    except BaseException:
        if created:
            # 本地仅清理由本次尝试新建的目录；S3 结果未知时不能套用同样的自动删除策略。
            shutil.rmtree(destination)
        if journal is not None:
            # 上传结果未知或对象被并发替换，都不能证明对象属于本次恢复，禁止无条件删除。
            # 保留隔离的恢复日志，交由管理员核查。
            journal['state'] = 'committed' if committed else 'failed'
            try:
                _write_journal(journal_path, journal)
            except OSError:
                pass  # 更新日志失败时，仍保留写入前的日志作为保守恢复依据。
        raise
