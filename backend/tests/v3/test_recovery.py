import hashlib
import json

import unittest
import tempfile
from pathlib import Path
from sqlalchemy import select

from app.v3.db import Database
from app.v3.models import Tenant, User, Version, Job, BackendInstance, Replica, WorkerPulse, MetricBucket, AlertState
from app.v3.storage import OriginalStore
from app.v3.recovery import backup_tenant, restore_tenant


ENGINES = []

def make_database(*args, **kwargs):
    db = Database(*args, **kwargs)
    ENGINES.append(db.engine)
    return db

def setup_source(tmp_path):
    db = make_database('sqlite:///' + str(tmp_path / 'source.db'), testing=True)
    db.migrate()
    store = OriginalStore(tmp_path / 'source_objects')
    data = b'private original'
    store.put('t/file', data)
    with db.transaction('t') as s:
        s.add_all([Tenant(id='t'), Tenant(id='other'), User(tenant_id='t', id='u'),
            User(tenant_id='other', id='other'),
            Version(id='v', tenant_id='t', document_id='d', sha256=hashlib.sha256(data).hexdigest(), object_key='t/file', filename='file'),
            Job(id='j', tenant_id='t', owner='u', kind='ingest', idempotency_key='j', payload={}, state='submitting', lease_token='old', lease_until=99999),
            BackendInstance(id='b', tenant_id='t', provider='dify', base_url='http://example', key_ref='reference', verified=True),
            Replica(id='r', tenant_id='t', version_id='v', instance_id='b', collection_id='c', remote_id='known', state='ready')])
    return db, store


def test_roundtrip_isolated_and_frozen(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    restore_tenant(target, 't', bundle, tmp_path / 'restored')
    with target.transaction('t') as s:
        assert s.get(Tenant, 'other') is None
        assert s.get(User, ('t', 'u')) is not None
        job = s.get(Job, 'j')
        assert job.state == 'needs_reconciliation'
        assert job.lease_token == '' and job.lease_until == 0
        assert not s.get(BackendInstance, 'b').verified
        assert s.get(Replica, 'r').remote_id == 'known'
    assert (tmp_path / 'restored/t/file').read_bytes() == b'private original'
    with unittest.TestCase().assertRaises(ValueError):
        restore_tenant(target, 't', bundle, tmp_path / 'again')


def test_corruption_and_overwrite_rejected(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    with unittest.TestCase().assertRaises(FileExistsError):
        backup_tenant(source, store, 't', bundle)
    (bundle / 'objects/0').write_bytes(b'corrupt')
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    with unittest.TestCase().assertRaises(ValueError):
        restore_tenant(target, 't', bundle, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()


def test_bad_original_rejected(tmp_path):
    source, store = setup_source(tmp_path)
    (tmp_path / 'source_objects/t/file').write_bytes(b'bad')
    with unittest.TestCase().assertRaises(ValueError):
        backup_tenant(source, store, 't', tmp_path / 'bundle')


def test_path_traversal_rejected(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    manifest['objects']['t/file']['file'] = '../source.db'
    (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    with unittest.TestCase().assertRaises(ValueError):
        restore_tenant(target, 't', bundle, tmp_path / 'restored')


def test_insert_conflict_rolls_back_objects_and_tenant(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    # Global primary key collision with another tenant must never overwrite it.
    with target.transaction('other') as session:
        session.add(Version(id='v', tenant_id='other', document_id='d', sha256='x', object_key='other/file', filename='x'))
    from sqlalchemy.exc import IntegrityError
    with unittest.TestCase().assertRaises(IntegrityError):
        restore_tenant(target, 't', bundle, tmp_path / 'restored')
    assert not (tmp_path / 'restored').exists()
    with target.transaction('t') as session:
        assert session.get(Tenant, 't') is None
        assert session.get(Version, 'v').tenant_id == 'other'


def test_cross_tenant_payload_and_existing_objects_rejected(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    existing = tmp_path / 'existing'
    existing.mkdir()
    (existing / 'keep').write_bytes(b'keep')
    with unittest.TestCase().assertRaises(FileExistsError):
        restore_tenant(target, 't', bundle, existing)
    assert (existing / 'keep').read_bytes() == b'keep'
    rows = json.loads((bundle / 'rows.json').read_text(encoding='utf-8'))
    rows['v3_users'][0]['tenant_id'] = 'other'
    raw = json.dumps(rows).encode()
    (bundle / 'rows.json').write_bytes(raw)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    manifest['rows_sha256'] = hashlib.sha256(raw).hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    with unittest.TestCase().assertRaises(ValueError):
        restore_tenant(target, 't', bundle, tmp_path / 'restored')


def test_recovery_discards_old_worker_metrics_and_alert_delivery_state(tmp_path):
    source, store = setup_source(tmp_path)
    with source.transaction('t') as session:
        session.add_all([
            WorkerPulse(tenant_id='t', worker_id='old', last_seen=9999999999, started_at=1),
            MetricBucket(tenant_id='t', stage='worker', outcome='ok', bucket=0, count=7),
            AlertState(tenant_id='t', code='worker_missing', event_id='old', changed_at=1, delivery='sent'),
        ])
    backup_tenant(source, store, 't', tmp_path / 'bundle')
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    restore_tenant(target, 't', tmp_path / 'bundle', tmp_path / 'restored')
    with target.transaction('t') as session:
        for model in (WorkerPulse, MetricBucket, AlertState):
            assert session.execute(select(model)).first() is None


def test_legacy_bundle_missing_runtime_tables_keeps_unknown_job_age(tmp_path):
    source, store = setup_source(tmp_path)
    bundle = tmp_path / 'bundle'
    backup_tenant(source, store, 't', bundle)
    rows = json.loads((bundle / 'rows.json').read_text(encoding='utf-8'))
    for model in (WorkerPulse, MetricBucket, AlertState):
        del rows[model.__tablename__]
    del rows[Job.__tablename__][0]['created_at']
    raw = json.dumps(rows).encode()
    (bundle / 'rows.json').write_bytes(raw)
    manifest = json.loads((bundle / 'manifest.json').read_text(encoding='utf-8'))
    manifest['rows_sha256'] = hashlib.sha256(raw).hexdigest()
    (bundle / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    target = make_database('sqlite:///' + str(tmp_path / 'target.db'), testing=True)
    target.migrate()
    restore_tenant(target, 't', bundle, tmp_path / 'restored')
    with target.transaction('t') as session:
        assert session.get(Job, 'j').created_at == 0

class RecoveryTests(unittest.TestCase):
    pass

for name, function in list(globals().items()):
    if name.startswith('test_'):
        def run(self, fn=function):
            with tempfile.TemporaryDirectory() as root:
                try:
                    fn(Path(root))
                finally:
                    for engine in ENGINES:
                        engine.dispose()
                    ENGINES.clear()
        setattr(RecoveryTests, name, run)

# Avoid leaving a second class alias for unittest's module discovery.
del function
