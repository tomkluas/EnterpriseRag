"""Isolated real S3 protocol probe. No business configuration or credentials are read."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'backend'))
from botocore.exceptions import ClientError
from app.v3.db import Database
from app.v3.domain import Domain, DomainError
from app.v3.models import Version, Tenant
from app.v3.orphan_gc import plan_orphans, apply_orphan_plan, namespace, current
from app.v3.recovery import _private_directory, backup_tenant, restore_tenant
from app.v3.storage import OriginalStore
from sqlalchemy import text
from sqlalchemy.engine import make_url


def port():
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', 0))
        return sock.getsockname()[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--minio', type=Path, required=True)
    parser.add_argument('--source-commit', required=True)
    parser.add_argument('--postgres-env-file', type=Path)
    parser.add_argument('--pilot-report', type=Path, default=Path('docs/testing/v3/live-pilot/weknora-local-pilot.json'))
    parser.add_argument('--report', type=Path, default=Path('docs/testing/v3/real-s3-2026-10-31.json'))
    args = parser.parse_args()
    binary = args.minio.resolve(strict=True)
    report = {'service': 'MinIO isolated protocol test only', 'source_commit': args.source_commit,
              'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
              'database': 'temporary SQLite testing=True', 'production_qualified': False,
              'retention_clock': 'explicit now + 8 days; no real 7-day wait', 'checks': []}
    hidden = {'creationflags': subprocess.CREATE_NO_WINDOW} if os.name == 'nt' else {}
    report['version'] = subprocess.check_output([str(binary), '--version'], text=True, **hidden).strip()
    root = Path(tempfile.gettempdir()) / ('enterprise-rag-real-s3-' + secrets.token_hex(12))
    _private_directory(root)
    process = None
    databases = []
    postgres_base = None
    postgres_url = None
    schemas = []
    env_keys = ('V3_SECRET_S3_ACCESS', 'V3_SECRET_S3_SECRET', 'V3_S3_ENDPOINT', 'V3_S3_REGION')
    previous = {key: os.environ.get(key) for key in env_keys}

    def database(name):
        if postgres_url is None:
            db = Database('sqlite:///' + str(root / (name + '.db')), testing=True)
        else:
            schema = 'real_s3_' + secrets.token_hex(16)
            with postgres_base.engine.begin() as connection:
                connection.execute(text('CREATE SCHEMA "' + schema + '"'))
            schemas.append(schema)
            scoped = postgres_url.update_query_dict({'options': '-csearch_path=' + schema})
            db = Database(scoped.render_as_string(hide_password=False))
        databases.append(db)
        db.migrate()
        return db

    def checked(name, condition, **details):
        report['checks'].append({'name': name, 'passed': bool(condition), **details})

    def passed(name, **details):
        checked(name, True, **details)

    def read_or_missing(objects, object_key):
        try:
            return objects.read(object_key)
        except ClientError as error:
            if error.response['ResponseMetadata']['HTTPStatusCode'] == 404:
                return None
            raise

    def rejected(action, exception, text=None):
        try:
            action()
        except exception as error:
            if text is not None:
                assert text in str(error), str(error)
            return error
        raise AssertionError('Expected rejection')

    try:
        if args.postgres_env_file is not None:
            from dotenv import dotenv_values
            configured = make_url(dotenv_values(args.postgres_env_file).get('V3_DATABASE_URL', ''))
            if (configured.host, configured.port, configured.database) != ('127.0.0.1', 18433, 'enterpriserag_v3'):
                raise ValueError('Expected isolated local V3 database endpoint')
            pilot = json.loads(args.pilot_report.read_text(encoding='utf-8-sig'))['test_database']
            if not isinstance(pilot, str) or not re.fullmatch('v3_live_[0-9a-f]{32}', pilot):
                raise ValueError('Expected existing synthetic live-pilot database')
            postgres_url = configured.set(database=pilot, query={})
            postgres_base = Database(postgres_url.render_as_string(hide_password=False))
            with postgres_base.engine.connect() as connection:
                if connection.scalar(text('SELECT current_database()')) != pilot:
                    raise ValueError('Connected to an unexpected database')
            report['database'] = 'PostgreSQL existing synthetic live-pilot database; new schemas only'
            report['test_database'] = pilot
        address, console = port(), port()
        while console == address:
            console = port()
        endpoint = f'http://127.0.0.1:{address}'
        access, secret = secrets.token_hex(12), secrets.token_hex(32)
        # Existing user MinIO configuration must never influence this isolated service.
        env = {key: value for key, value in os.environ.items() if not key.upper().startswith('MINIO_')}
        env.update(MINIO_ROOT_USER=access, MINIO_ROOT_PASSWORD=secret,
                   MINIO_BROWSER='off', MINIO_UPDATE='off')
        os.environ.update(V3_SECRET_S3_ACCESS=access, V3_SECRET_S3_SECRET=secret,
                          V3_S3_ENDPOINT=endpoint, V3_S3_REGION='us-east-1')
        with (root / 'server.log').open('wb') as log:
            process = subprocess.Popen([str(binary), '--config-dir', str(root / 'config'),
                                        '--certs-dir', str(root / 'certs'), 'server', '--address', f'127.0.0.1:{address}',
                                        '--console-address', f'127.0.0.1:{console}', str(root / 'data')],
                                       env=env, stdout=log, stderr=log, **hidden)
        deadline = time.monotonic() + 60
        while True:
            try:
                with urllib.request.urlopen(endpoint + '/minio/health/ready', timeout=1) as response:
                    assert response.status == 200
                break
            except Exception:
                if process.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError('Isolated MinIO did not become ready; private log withheld')
                time.sleep(.2)
        passed('real_service_ready_on_loopback')
        bucket = 'validation-' + secrets.token_hex(8)
        prefix = 'run-' + secrets.token_hex(8)
        location = f's3://{bucket}/{prefix}'
        store = OriginalStore(location)
        client = store.client
        client.create_bucket(Bucket=bucket)
        source = database('source')
        domain = Domain(source, location)
        domain.bootstrap('t', 'admin')
        content = b'isolated real-S3 original\x00\xff'
        key = namespace('t') + secrets.token_hex(16)
        store.put(key, content)
        with source.transaction('t') as session:
            session.add(Version(id='v', tenant_id='t', document_id='d', filename='probe.bin',
                                object_key=key, sha256=hashlib.sha256(content).hexdigest()))
        bundle = root / 'bundle'
        manifest = backup_tenant(source, store, 't', bundle)
        assert manifest['objects'][key]['sha256'] == hashlib.sha256(content).hexdigest()
        target = database('target')
        restored_location = f's3://{bucket}/restored-{secrets.token_hex(8)}'
        restored = restore_tenant(target, 't', bundle, restored_location)
        assert restored['objects'] == 1
        remote = OriginalStore(restored_location)
        assert remote.read(key) == content
        with target.transaction('t') as session:
            assert session.get(Version, 'v').sha256 == hashlib.sha256(remote.read(key)).hexdigest()
        passed('backup_restore_real_s3_hash_roundtrip', sha256=hashlib.sha256(content).hexdigest())
        report['restoration_passed'] = True
        rejected(lambda: restore_tenant(database('occupied-prefix'), 't', bundle, restored_location), ValueError, 'empty')
        assert remote.read(key) == content
        passed('restore_nonempty_prefix_rejected_existing_object_preserved')
        error = rejected(lambda: remote.put(key, b'overwrite'), ClientError)
        assert error.response['ResponseMetadata']['HTTPStatusCode'] == 412
        assert remote.read(key) == content
        passed('conditional_put_if_none_match_412')
        try:
            response = client.delete_object(Bucket=bucket, Key=remote.key(key), IfMatch='"wrong-etag"')
            delete_status = response['ResponseMetadata']['HTTPStatusCode']
        except ClientError as error:
            delete_status = error.response['ResponseMetadata']['HTTPStatusCode']
        preserved = read_or_missing(remote, key) == content
        checked('conditional_delete_if_match_412', delete_status == 412 and preserved,
                http_status=delete_status, original_preserved=preserved)
        report['conditional_delete_supported'] = delete_status == 412 and preserved
        # Recreate only our disposable probe object so remaining checks can still run.
        if not preserved:
            remote.put(key, content)
        orphan = namespace('t') + secrets.token_hex(16)
        store.put(orphan, b'orphan')
        now = time.time() + 8 * 86400
        plan = plan_orphans(domain, 't', 'admin', now=now)
        assert [item['key'] for item in plan['candidates']] == [orphan]
        assert store.read(orphan) == b'orphan'
        report['gc_timestamp_observation'] = {
            'list_modified_at': plan['candidates'][0]['modified_at'],
            'head_modified_at': current(domain.objects, orphan)['modified_at']}
        if not report['conditional_delete_supported']:
            delete_requests = []
            actual_delete = domain.objects.client.delete_object

            def observe_delete(**kwargs):
                delete_requests.append(kwargs['Key'])
                return actual_delete(**kwargs)

            with patch.object(domain.objects.client, 'delete_object', side_effect=observe_delete):
                error = rejected(lambda: apply_orphan_plan(domain, 't', 'admin', plan, now=now), DomainError, '条件删除')
            assert store.read(orphan) == b'orphan' and store.read(key) == content
            canary_prefix = store.key('__orphan_gc_canary__') + '/'
            assert delete_requests and all(item.startswith(canary_prefix) for item in delete_requests)
            residual = list(client.get_paginator('list_objects_v2').paginate(Bucket=bucket, Prefix=canary_prefix))
            report['canary_cleanup'] = {
                'candidate_delete_requests': 0, 'synthetic_probe_delete_requests': len(delete_requests),
                'residual_objects_observed': sum(len(page.get('Contents', [])) for page in residual),
                'unconditional_delete_used': False,
                'final_cleanup': 'only owned temporary MinIO process and entire private data directory removed'}
            report['gc_safely_refused'] = True
            passed('orphan_gc_unsupported_service_refused_before_candidate_delete', rejection=str(error))
        else:
            result = apply_orphan_plan(domain, 't', 'admin', plan, now=now)
            assert result['deleted'] == 1 and result['failed'] == 0
            assert store.read(key) == content
            passed('orphan_gc_real_plan_apply_preserves_referenced_original', deleted=1)
            raced = namespace('t') + secrets.token_hex(16)
            store.put(raced, b'old')
            plan = plan_orphans(domain, 't', 'admin', now=now)
            original_delete = domain.objects.client.delete_object
            status = []

            def replace_before_delete(**kwargs):
                if kwargs['Key'] == domain.objects.key(raced):
                    client.put_object(Bucket=bucket, Key=kwargs['Key'], Body=b'concurrent replacement')
                try:
                    return original_delete(**kwargs)
                except ClientError as error:
                    if kwargs['Key'] == domain.objects.key(raced):
                        status.append(error.response['ResponseMetadata']['HTTPStatusCode'])
                    raise

            with patch.object(domain.objects.client, 'delete_object', side_effect=replace_before_delete):
                result = apply_orphan_plan(domain, 't', 'admin', plan, now=now)
            preserved = read_or_missing(store, raced) == b'concurrent replacement'
            checked('orphan_gc_concurrent_replacement_real_delete_412_preserved',
                    result['failed'] == 1 and result['deleted'] == 0 and status == [412] and preserved,
                    result=result, conditional_error_http_statuses=status, concurrent_object_preserved=preserved)
        client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': 'Enabled'})
        for versioning in ('Enabled', 'Suspended'):
            if versioning == 'Suspended':
                client.put_bucket_versioning(Bucket=bucket, VersioningConfiguration={'Status': versioning})
            rejected(lambda: restore_tenant(database('versioning-' + versioning), 't', bundle,
                                            f's3://{bucket}/versioned-{secrets.token_hex(8)}'), ValueError, 'version')
            rejected(lambda: plan_orphans(domain, 't', 'admin', now=now), DomainError)
            passed('versioning_' + versioning.lower() + '_restore_and_gc_rejected')
        report['passed'] = all(item['passed'] for item in report['checks'])
        report['failed_checks'] = [item['name'] for item in report['checks'] if not item['passed']]
    except Exception as error:
        report['passed'] = False
        report['failure'] = {'type': type(error).__name__, 'message': str(error)}
        raise
    finally:
        cleanup_errors = []

        def cleanup_step(label, action):
            try:
                action()
            except Exception as error:
                cleanup_errors.append({'step': label, 'error_type': type(error).__name__})

        def stop_service():
            process.terminate()
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=10)

        if process is not None and process.poll() is None:
            cleanup_step('stop_owned_minio', stop_service)
        for db in databases:
            cleanup_step('dispose_owned_database', db.engine.dispose)
        if postgres_base is not None:
            report['postgres_schemas_dropped'] = 0

            def drop_schema(schema):
                if not re.fullmatch('real_s3_[0-9a-f]{32}', schema):
                    raise ValueError('Refusing to drop an unexpected schema')
                with postgres_base.engine.begin() as connection:
                    connection.execute(text('DROP SCHEMA "' + schema + '" CASCADE'))
                report['postgres_schemas_dropped'] += 1

            for schema in schemas:
                cleanup_step('drop_owned_schema:' + schema, lambda schema=schema: drop_schema(schema))
            cleanup_step('dispose_postgres_base', postgres_base.engine.dispose)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        cleanup_step('remove_owned_private_directory', lambda: shutil.rmtree(root))
        if cleanup_errors:
            report['passed'] = False
            report['cleanup_errors'] = cleanup_errors
            report['cleanup'] = 'cleanup incomplete; inspect cleanup_errors'
        else:
            report['cleanup'] = 'owned process stopped; private temporary service data, databases/schemas, credentials and bundles removed'
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    print(json.dumps({'passed': report['passed'], 'checks': len(report['checks']), 'report': str(args.report)}))
    if not report['passed']:
        raise SystemExit(1)


if __name__ == '__main__':
    main()
