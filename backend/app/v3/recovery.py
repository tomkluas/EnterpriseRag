"""Offline tenant logical recovery. Never run against a serving restore target."""
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
RUNTIME_TABLES = ('v3_worker_pulses', 'v3_metric_buckets', 'v3_alert_states')


def _digest(data):
    return hashlib.sha256(data).hexdigest()


def _encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def _private_directory(path):
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


def _safe_key(key):
    if not isinstance(key, str) or not key or '\\' in key or ':' in key:
        raise ValueError('Invalid object key')
    path = PurePosixPath(key)
    if path.is_absolute() or any(p in ('', '.', '..') for p in key.split('/')):
        raise ValueError('Invalid object key')
    return key


def _read_file(root, relative):
    _safe_key(relative)
    path = root / relative
    if any(p.is_symlink() for p in [path, *path.parents] if p != root.parent):
        raise ValueError('Symlink in recovery bundle')
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError('Recovery path escaped bundle')
    return path.read_bytes()


def _empty_s3_destination(location):
    """Fail closed: the restore target must be an unversioned, empty namespace."""
    uri = urlsplit(location)
    prefix = uri.path[1:].rstrip('/')
    if not prefix or '@' in uri.netloc:
        raise ValueError('Recovery requires an explicit private S3 prefix')
    _safe_key(prefix)
    store = OriginalStore(location)
    versioning = store.client.get_bucket_versioning(Bucket=store.bucket)
    if versioning.get('Status'):
        raise ValueError('S3 versioned buckets are not supported by logical recovery')
    pages = store.client.get_paginator('list_objects_v2').paginate(
        Bucket=store.bucket, Prefix=store.prefix + '/')
    for page in pages:
        if page.get('Contents'):
            raise ValueError('S3 recovery prefix must be empty')
    return store


def _write_journal(directory, journal):
    """Persist before writes; a crash may leave an unknown outcome, never permission to delete."""
    temporary = directory / 'manifest.tmp'
    with temporary.open('wb') as stream:
        stream.write(_encode(journal))
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(directory / 'manifest.json')


def _upload_recovery_objects(store, contents, journal_path, journal):
    for entry in journal['objects']:
        key = entry['key']
        entry['state'] = 'write_unknown'
        _write_journal(journal_path, journal)
        store.put(key, contents[key])
        entry['state'] = 'written'
        _write_journal(journal_path, journal)
        if _digest(store.read(key)) != entry['sha256']:
            raise ValueError('Restored original checksum mismatch')
        entry['state'] = 'verified'
        _write_journal(journal_path, journal)


def backup_tenant(db, originals: OriginalStore, tenant_id: str, destination) -> dict:
    """Create a new private bundle, holding the tenant coordination lock throughout."""
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
            for version in rows[Version.__tablename__]:
                key = _safe_key(version['object_key'])
                if key in objects:
                    if objects[key]['sha256'] != version['sha256']:
                        raise ValueError('Conflicting original hashes')
                    continue
                # Local symlinks must not expose files outside the configured original root.
                if not originals.client:
                    _read_file(originals.root, key)
                data = originals.read(key)
                if _digest(data) != version['sha256']:
                    raise ValueError('Original checksum mismatch')
                relative = f'objects/{len(objects)}'
                (destination / relative).write_bytes(data)
                objects[key] = {'file': relative, 'sha256': _digest(data)}
            data = _encode(rows)
            (destination / 'rows.json').write_bytes(data)
            manifest = {'format': 1, 'tenant_id': tenant_id, 'rows_sha256': _digest(data), 'objects': objects}
            (destination / 'manifest.json').write_bytes(_encode(manifest))
            return manifest
    except BaseException:
        shutil.rmtree(destination)
        raise


def restore_tenant(db, tenant_id: str, bundle, original_destination) -> dict:
    """Restore into an empty tenant and NEW local directory or empty S3 prefix, offline."""
    bundle = Path(bundle).absolute()
    manifest = json.loads(_read_file(bundle, 'manifest.json'))
    if manifest.get('format') != 1 or manifest.get('tenant_id') != tenant_id:
        raise ValueError('Bundle format or tenant mismatch')
    raw = _read_file(bundle, 'rows.json')
    if _digest(raw) != manifest['rows_sha256']:
        raise ValueError('Database checksum mismatch')
    rows = json.loads(raw)
    # Explicit additive compatibility only; all other schema differences fail closed.
    for name in RUNTIME_TABLES:
        if name in TABLES:
            rows.setdefault(name, [])
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
            # Runtime observations from before recovery must never become live health.
            for name in RUNTIME_TABLES:
                if name in rows:
                    rows[name] = []
            for row in rows[Job.__tablename__]:
                row['lease_token'] = ''; row['lease_until'] = 0
                if row['state'] not in ('completed', 'cancelled', 'failed'):
                    row['state'] = 'needs_reconciliation'
            for row in rows[BackendInstance.__tablename__]:
                row['verified'] = False
                row['report'] = {}
            for row in rows[Message.__tablename__]:
                if row['state'] == 'processing':
                    row['state'] = 'failed'
            for row in rows[BackendSwitch.__tablename__]:
                row['state'] = 'invalidated'
            for name, table in TABLES.items():
                if rows[name]:
                    session.execute(table.insert(), rows[name])
        committed = True
        result = {'tenant_id': tenant_id, 'rows': sum(map(len, rows.values())), 'objects': len(contents)}
        if journal is not None:
            journal['state'] = 'committed'
            _write_journal(journal_path, journal)
            result['recovery_manifest'] = str(journal_path / 'manifest.json')
        return result
    except BaseException:
        if created:
            shutil.rmtree(destination)
        if journal is not None:
            # No unconditional S3 DELETE: an unknown PUT or concurrent replacement is
            # not proof of ownership. Keep this attempt isolated for operator review.
            journal['state'] = 'committed' if committed else 'failed'
            try:
                _write_journal(journal_path, journal)
            except OSError:
                pass  # The pre-write journal remains the conservative recovery record.
        raise
