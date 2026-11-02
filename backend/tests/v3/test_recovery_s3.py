# 验证 S3 恢复的条件写入、读回核验与失败隔离；Stubber 严格约束请求顺序，不连接真实 S3。
"""Protocol-level recovery tests: all storage calls are botocore stubs."""
import hashlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import boto3
from botocore.response import StreamingBody
from botocore.stub import Stubber
from botocore.exceptions import ClientError
from sqlalchemy.exc import IntegrityError

from app.v3.models import Tenant, Version
from app.v3.recovery import backup_tenant, restore_tenant
from test_recovery import ENGINES, make_database, setup_source


class S3RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        source, store = setup_source(self.root)
        self.bundle = self.root / 'bundle'
        backup_tenant(source, store, 't', self.bundle)
        self.target = make_database('sqlite:///' + str(self.root / 'target.db'), testing=True)
        self.target.migrate()
        self.client = boto3.client('s3', region_name='us-east-1', aws_access_key_id='fake', aws_secret_access_key='fake')
        self.stub = Stubber(self.client)
        self.stub.activate()
        self.addCleanup(self.stub.deactivate)
        self.env = patch.dict('os.environ', {'V3_SECRET_S3_ACCESS': 'fake', 'V3_SECRET_S3_SECRET': 'fake'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.factory = patch('app.v3.storage.boto3.client', return_value=self.client)
        self.factory.start()
        self.addCleanup(self.factory.stop)
        self.destination = 's3://restore-bucket/recovery/run1'

    def tearDown(self):
        for engine in ENGINES:
            engine.dispose()
        ENGINES.clear()
        self.temp.cleanup()

    # 逐条预设目标前缀为空的响应；分页场景必须读完全部页，不能把首页为空误判为整个恢复空间为空。
    def empty(self, pages=False):
        self.stub.add_response('get_bucket_versioning', {}, {'Bucket': 'restore-bucket'})
        params = {'Bucket': 'restore-bucket', 'Prefix': 'recovery/run1/'}
        self.stub.add_response('list_objects_v2', {'IsTruncated': pages, 'NextContinuationToken': 'next'} if pages else {'IsTruncated': False}, params)
        if pages:
            self.stub.add_response('list_objects_v2', {'IsTruncated': False}, dict(params, ContinuationToken='next'))

    def upload(self, read=b'private original'):
        data = b'private original'
        self.stub.add_response('put_object', {'ETag': '"new"'}, {
            'Bucket': 'restore-bucket', 'Key': 'recovery/run1/t/file', 'Body': data,
            'IfNoneMatch': '*', 'Metadata': {'sha256': hashlib.sha256(data).hexdigest()}})
        self.stub.add_response('get_object', {'Body': StreamingBody(io.BytesIO(read), len(read))}, {
            'Bucket': 'restore-bucket', 'Key': 'recovery/run1/t/file'})

    # 读取恢复隔离记录，确认失败后仍保留足够证据定位本轮写入对象，而不是随意删除未知远端内容。
    def journal(self):
        return json.loads(next(self.root.glob('recovery-attempt-*/manifest.json')).read_text(encoding='utf-8'))

    def test_empty_paginated_prefix_roundtrip_reads_back_and_commits(self):
        self.empty(pages=True)
        self.upload()
        result = restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertEqual(result['objects'], 1)
        self.assertEqual(self.journal()['state'], 'committed')
        with self.target.transaction('t') as session:
            self.assertIsNotNone(session.get(Tenant, 't'))
        self.stub.assert_no_pending_responses()

    def test_nonempty_second_page_rejects_without_touching_existing_objects(self):
        self.stub.add_response('get_bucket_versioning', {}, {'Bucket': 'restore-bucket'})
        params = {'Bucket': 'restore-bucket', 'Prefix': 'recovery/run1/'}
        self.stub.add_response('list_objects_v2', {'IsTruncated': True, 'NextContinuationToken': 'next'}, params)
        self.stub.add_response('list_objects_v2', {'Contents': [{'Key': 'recovery/run1/other/keep'}]}, dict(params, ContinuationToken='next'))
        with self.assertRaisesRegex(ValueError, 'empty'):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.stub.assert_no_pending_responses()

    def test_versioned_or_suspended_buckets_rejected(self):
        for status in ('Enabled', 'Suspended'):
            self.stub.add_response('get_bucket_versioning', {'Status': status}, {'Bucket': 'restore-bucket'})
            with self.assertRaisesRegex(ValueError, 'version'):
                restore_tenant(self.target, 't', self.bundle, self.destination)

    def test_bucket_root_is_not_a_recovery_namespace(self):
        with self.assertRaisesRegex(ValueError, 'prefix'):
            restore_tenant(self.target, 't', self.bundle, 's3://restore-bucket')

    def test_readback_corruption_rolls_back_and_preserves_isolation_journal(self):
        self.empty()
        self.upload(read=b'corrupted')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertEqual(self.journal()['state'], 'failed')
        self.assertEqual(self.journal()['objects'][0]['state'], 'written')
        with self.target.transaction('t') as session:
            self.assertIsNone(session.get(Tenant, 't'))
        self.stub.assert_no_pending_responses()

    def test_database_collision_never_deletes_remote_keys_and_preserves_manifest(self):
        with self.target.transaction('other') as session:
            session.add(Version(id='v', tenant_id='other', document_id='d', sha256='x', object_key='other/file', filename='x'))
        self.empty()
        self.upload()
        with self.assertRaises(IntegrityError):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertEqual(self.journal()['state'], 'failed')
        self.assertEqual(self.journal()['objects'][0]['state'], 'verified')
        with self.target.transaction('t') as session:
            self.assertIsNone(session.get(Tenant, 't'))
            self.assertEqual(session.get(Version, 'v').tenant_id, 'other')
        self.stub.assert_no_pending_responses()

    def test_conditional_write_conflict_keeps_unknown_object_untouched(self):
        self.empty()
        self.stub.add_client_error('put_object', 'PreconditionFailed', http_status_code=412)
        with self.assertRaises(ClientError):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertEqual(self.journal()['objects'][0]['state'], 'write_unknown')
        self.stub.assert_no_pending_responses()

    def test_corrupt_bundle_is_rejected_before_any_s3_calls(self):
        (self.bundle / 'objects/0').write_bytes(b'tampered')
        with self.assertRaisesRegex(ValueError, 'checksum'):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertFalse(list(self.root.glob('recovery-attempt-*')))

    def test_foreign_tenant_object_key_rejected_even_with_valid_bundle_hashes(self):
        rows_path = self.bundle / 'rows.json'
        rows = json.loads(rows_path.read_text(encoding='utf-8'))
        rows[Version.__tablename__][0]['object_key'] = 'other/keep'
        raw = json.dumps(rows).encode()
        rows_path.write_bytes(raw)
        manifest_path = self.bundle / 'manifest.json'
        manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
        manifest['rows_sha256'] = hashlib.sha256(raw).hexdigest()
        manifest['objects']['other/keep'] = manifest['objects'].pop('t/file')
        manifest_path.write_text(json.dumps(manifest), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'tenant namespace'):
            restore_tenant(self.target, 't', self.bundle, self.destination)

    def test_list_access_denied_never_guesses_empty(self):
        self.stub.add_response('get_bucket_versioning', {}, {'Bucket': 'restore-bucket'})
        self.stub.add_client_error('list_objects_v2', 'AccessDenied', http_status_code=403,
                                   expected_params={'Bucket': 'restore-bucket', 'Prefix': 'recovery/run1/'})
        with self.assertRaises(ClientError):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertFalse(list(self.root.glob('recovery-attempt-*')))
        self.stub.assert_no_pending_responses()

    def test_get_access_denied_keeps_written_object_isolated_and_database_empty(self):
        self.empty()
        data = b'private original'
        self.stub.add_response('put_object', {}, {'Bucket': 'restore-bucket', 'Key': 'recovery/run1/t/file',
            'Body': data, 'IfNoneMatch': '*', 'Metadata': {'sha256': hashlib.sha256(data).hexdigest()}})
        self.stub.add_client_error('get_object', 'AccessDenied', http_status_code=403,
                                   expected_params={'Bucket': 'restore-bucket', 'Key': 'recovery/run1/t/file'})
        with self.assertRaises(ClientError):
            restore_tenant(self.target, 't', self.bundle, self.destination)
        self.assertEqual(self.journal()['objects'][0]['state'], 'written')
        with self.target.transaction('t') as session:
            self.assertIsNone(session.get(Tenant, 't'))
        self.stub.assert_no_pending_responses()

    def test_private_manifest_acl_failure_happens_before_remote_writes(self):
        self.empty()
        with patch('app.v3.recovery._private_directory', side_effect=PermissionError('ACL denied')):
            with self.assertRaises(PermissionError):
                restore_tenant(self.target, 't', self.bundle, self.destination)
        with self.target.transaction('t') as session:
            self.assertIsNone(session.get(Tenant, 't'))
        self.stub.assert_no_pending_responses()
