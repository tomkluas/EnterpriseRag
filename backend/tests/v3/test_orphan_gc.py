import hashlib
import os
import time
import tempfile
import unittest
from pathlib import Path
from app.v3.db import Database
from app.v3.domain import Domain,DomainError
from app.v3.models import Version,Tenant,Audit


class OrphanGcTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.db=Database('sqlite:///'+str(self.root/'test.db'),testing=True);self.db.migrate()
        self.domain=Domain(self.db,self.root/'objects');self.domain.bootstrap('t','admin')
        self.prefix=hashlib.sha256(b't').hexdigest()
        self.space=self.domain.create_space('t','admin','test')
        self.old=self.prefix+'/'+'a'*32
        self.domain.objects.put(self.old,b'orphan')
        old=time.time()-10*86400;os.utime(self.domain.objects.root/self.old,(old,old))

    def tearDown(self):self.db.engine.dispose();self.temp.cleanup()

    def plan(self):
        from app.v3.orphan_gc import plan_orphans
        return plan_orphans(self.domain,'t','admin',retention_days=7)

    def apply(self,plan):
        from app.v3.orphan_gc import apply_orphan_plan
        return apply_orphan_plan(self.domain,'t','admin',plan)

    def test_only_aged_unreferenced_namespaced_objects_are_planned_and_deleted(self):
        current=self.domain.receive('t','admin',self.space,'current.txt',b'current')
        recent=self.prefix+'/'+'b'*32;self.domain.objects.put(recent,b'recent')
        other=hashlib.sha256(b'other').hexdigest()+'/'+'c'*32;self.domain.objects.put(other,b'other')
        plan=self.plan()
        self.assertEqual([x['key'] for x in plan['candidates']],[self.old])
        self.assertTrue((self.domain.objects.root/self.old).exists())
        self.assertEqual(self.apply(plan)['deleted'],1)
        self.assertFalse((self.domain.objects.root/self.old).exists())
        self.assertTrue((self.domain.objects.root/recent).exists());self.assertTrue((self.domain.objects.root/other).exists())
        self.assertEqual(self.domain.read_original('t','admin',current['document_id']),b'current')
        with self.db.transaction('t') as s:self.assertEqual(s.query(Audit).filter_by(action='original.orphan_delete').count(),1)

    def test_new_reference_even_without_revision_change_blocks_deletion(self):
        plan=self.plan()
        with self.db.transaction('t') as s:
            s.add(Version(id='late',tenant_id='t',document_id='d',filename='late.txt',object_key=self.old,sha256=hashlib.sha256(b'orphan').hexdigest()))
        with self.assertRaises(DomainError):self.apply(plan)
        self.assertTrue((self.domain.objects.root/self.old).exists())

    def test_modified_object_or_catalog_change_invalidates_plan(self):
        plan=self.plan();(self.domain.objects.root/self.old).write_bytes(b'changed')
        with self.assertRaises(DomainError):self.apply(plan)
        plan=self.plan()
        with self.db.transaction('t') as s:s.get(Tenant,'t').catalog+=1
        with self.assertRaises(DomainError):self.apply(plan)

    def test_cross_tenant_paths_permissions_and_short_retention_rejected(self):
        from app.v3.orphan_gc import plan_orphans
        self.domain.add_user('t','admin','reader')
        with self.assertRaises(DomainError):plan_orphans(self.domain,'t','reader')
        with self.assertRaises(DomainError):plan_orphans(self.domain,'t','admin',retention_days=0)
        plan=self.plan();plan['candidates'][0]['key']='../outside'
        with self.assertRaises(DomainError):self.apply(plan)
        self.assertTrue((self.domain.objects.root/self.old).exists())

    def test_late_missing_candidate_is_idempotently_reported(self):
        plan=self.plan();self.apply(plan)
        result=self.apply(plan)
        self.assertEqual(result['deleted'],0);self.assertEqual(result['already_absent'],1)

    def test_plan_expiry_and_symlink_are_rejected(self):
        from app.v3.orphan_gc import apply_orphan_plan
        plan=self.plan()
        with self.assertRaises(DomainError):apply_orphan_plan(self.domain,'t','admin',plan,now=plan['created_at']+86401)

    def test_file_delete_failure_keeps_durable_intent_and_a_failed_result(self):
        from unittest.mock import patch
        from app.v3.models import Job
        plan=self.plan()
        with patch('pathlib.Path.unlink',side_effect=PermissionError('denied')):
            result=self.apply(plan)
        self.assertEqual(result['deleted'],0);self.assertEqual(result['failed'],1)
        with self.db.transaction('t') as s:
            job=s.get(Job,result['intent_job_id'])
            self.assertEqual(job.state,'failed')
            self.assertEqual(job.payload['plan']['plan_id'],plan['plan_id'])
        self.assertTrue((self.domain.objects.root/self.old).exists())

    def test_s3_versioned_bucket_refuses_gc_before_listing(self):
        import boto3
        from botocore.stub import Stubber
        from unittest.mock import patch
        from app.v3.storage import OriginalStore
        client=boto3.client('s3',aws_access_key_id='fixture',aws_secret_access_key='fixture',region_name='us-east-1')
        with Stubber(client) as stub,patch.dict(os.environ,{'V3_SECRET_S3_ACCESS':'fixture','V3_SECRET_S3_SECRET':'fixture'}),patch('app.v3.storage.boto3.client',return_value=client):
            self.domain.objects=OriginalStore('s3://test-bucket/isolated')
            stub.add_response('get_bucket_versioning',{'Status':'Enabled'},{'Bucket':'test-bucket'})
            with self.assertRaises(DomainError):self.plan()
            stub.assert_no_pending_responses()

    def test_s3_conditional_delete_and_precondition_failure_are_recorded(self):
        import boto3
        import io
        from datetime import datetime,timezone
        from botocore.stub import Stubber,ANY
        from unittest.mock import patch
        from app.v3.storage import OriginalStore
        for conflict in (False,True):
            with self.subTest(conflict=conflict):
                client=boto3.client('s3',aws_access_key_id='fixture',aws_secret_access_key='fixture',region_name='us-east-1')
                changed=datetime.fromtimestamp(time.time()-10*86400,timezone.utc).replace(microsecond=0)
                listed=changed.replace(microsecond=920000)
                args={'Bucket':'test-bucket','Key':'isolated/'+self.old}
                head={'LastModified':changed,'ContentLength':6,'ETag':'"etag"'}
                with Stubber(client) as stub,patch.dict(os.environ,{'V3_SECRET_S3_ACCESS':'fixture','V3_SECRET_S3_SECRET':'fixture'}),patch('app.v3.storage.boto3.client',return_value=client):
                    self.domain.objects=OriginalStore('s3://test-bucket/isolated')
                    stub.add_response('get_bucket_versioning',{}, {'Bucket':'test-bucket'})
                    stub.add_response('list_objects_v2',{'Contents':[{'Key':args['Key'],'LastModified':listed,'Size':6,'ETag':'"etag"'}]}, {'Bucket':'test-bucket','Prefix':'isolated/'+self.prefix+'/'})
                    stub.add_response('get_object',{'Body':io.BytesIO(b'orphan')},args)
                    plan=self.plan()
                    for phase in range(2):
                        stub.add_response('get_bucket_versioning',{}, {'Bucket':'test-bucket'})
                        stub.add_response('head_object',head,args)
                        stub.add_response('get_object',{'Body':io.BytesIO(b'orphan')},args)
                        if phase==0:
                            from app.v3.orphan_gc import CANARY_CONTENT
                            probe={'Bucket':'test-bucket','Key':ANY}
                            stub.add_response('put_object',{'ETag':'"probe-etag"'},{**probe,'Body':CANARY_CONTENT,'IfNoneMatch':'*'})
                            stub.add_client_error('delete_object','PreconditionFailed',http_status_code=412,expected_params={**probe,'IfMatch':ANY})
                            stub.add_response('get_object',{'Body':io.BytesIO(CANARY_CONTENT)},probe)
                            stub.add_response('delete_object',{}, {**probe,'IfMatch':'"probe-etag"'})
                            stub.add_client_error('head_object','NoSuchKey',http_status_code=404,expected_params=probe)
                    stub.add_response('head_object',head,args)
                    if conflict:stub.add_client_error('delete_object','PreconditionFailed',http_status_code=412,expected_params={**args,'IfMatch':'"etag"'})
                    else:
                        stub.add_response('delete_object',{}, {**args,'IfMatch':'"etag"'})
                        stub.add_client_error('head_object','NoSuchKey',http_status_code=404,expected_params=args)
                    result=self.apply(plan)
                    self.assertEqual(result['deleted'],0 if conflict else 1)
                    self.assertEqual(result['failed'],1 if conflict else 0)
                    stub.assert_no_pending_responses()

    def test_s3_apply_refuses_service_ignoring_ifmatch_before_candidate_delete(self):
        import io
        from datetime import datetime,timezone
        from unittest.mock import patch
        import boto3
        from botocore.stub import Stubber,ANY
        from app.v3.storage import OriginalStore
        client=boto3.client('s3',aws_access_key_id='fixture',aws_secret_access_key='fixture',region_name='us-east-1')
        changed=datetime.fromtimestamp(time.time()-10*86400,timezone.utc).replace(microsecond=0)
        args={'Bucket':'test-bucket','Key':'isolated/'+self.old}
        head={'LastModified':changed,'ContentLength':6,'ETag':'"etag"'}
        with Stubber(client) as stub,patch.dict(os.environ,{'V3_SECRET_S3_ACCESS':'fixture','V3_SECRET_S3_SECRET':'fixture'}),patch('app.v3.storage.boto3.client',return_value=client):
            self.domain.objects=OriginalStore('s3://test-bucket/isolated')
            stub.add_response('get_bucket_versioning',{}, {'Bucket':'test-bucket'})
            stub.add_response('list_objects_v2',{'Contents':[{'Key':args['Key'],'LastModified':changed,'Size':6,'ETag':'"etag"'}]}, {'Bucket':'test-bucket','Prefix':'isolated/'+self.prefix+'/'})
            stub.add_response('get_object',{'Body':io.BytesIO(b'orphan')},args)
            plan=self.plan()
            stub.add_response('get_bucket_versioning',{}, {'Bucket':'test-bucket'})
            stub.add_response('head_object',head,args)
            stub.add_response('get_object',{'Body':io.BytesIO(b'orphan')},args)
            stub.add_response('put_object',{'ETag':'"probe-etag"'},{'Bucket':'test-bucket','Key':ANY,'Body':ANY,'IfNoneMatch':'*'})
            stub.add_response('delete_object',{'ResponseMetadata':{'HTTPStatusCode':204}}, {'Bucket':'test-bucket','Key':ANY,'IfMatch':ANY})
            with self.assertRaisesRegex(DomainError,'条件删除'):
                self.apply(plan)
            stub.assert_no_pending_responses()

    def test_s3_fractional_timestamp_does_not_shorten_retention(self):
        from types import SimpleNamespace
        from unittest.mock import patch
        from app.v3.orphan_gc import validate,digest
        plan=self.plan()
        base=1700000000
        listed=base+0.92
        self.domain.objects=SimpleNamespace(client=True,location='s3://test-bucket/isolated',read=lambda key:b'orphan')
        plan['store_fingerprint']=digest(self.domain.objects.location)
        plan['created_at']=base+7*86400
        item=plan['candidates'][0]
        item.pop('mtime_ns')
        item.update(modified_at=listed,etag='"etag"')
        plan['plan_sha256']=digest({k:v for k,v in plan.items() if k!='plan_sha256'})
        head={'modified_at':float(base),'size':6,'etag':'"etag"'}
        with patch('app.v3.orphan_gc.current',return_value=head),self.db.transaction('t') as session:
            with self.assertRaises(DomainError):
                validate(self.domain,session,'t','admin',plan,base+7*86400+0.50)
            validate(self.domain,session,'t','admin',plan,listed+7*86400)

    def test_s3_probe_failures_preserve_residue_key_without_unconditional_cleanup(self):
        import io
        import boto3
        from botocore.stub import Stubber,ANY
        from unittest.mock import patch
        from app.v3.storage import OriginalStore
        from app.v3.orphan_gc import require_conditional_delete,CANARY_CONTENT
        for failure in ('put_denied','delete_denied','read_corrupt','read_missing','cleanup_denied','cleanup_still_present'):
            with self.subTest(failure=failure):
                client=boto3.client('s3',aws_access_key_id='fixture',aws_secret_access_key='fixture',region_name='us-east-1')
                with Stubber(client) as stub,patch.dict(os.environ,{'V3_SECRET_S3_ACCESS':'fixture','V3_SECRET_S3_SECRET':'fixture'}),patch('app.v3.storage.boto3.client',return_value=client):
                    store=OriginalStore('s3://test-bucket/isolated')
                    probe={'Bucket':'test-bucket','Key':ANY}
                    put={**probe,'Body':CANARY_CONTENT,'IfNoneMatch':'*'}
                    if failure=='put_denied':
                        stub.add_client_error('put_object','AccessDenied',http_status_code=403,expected_params=put)
                    else:
                        stub.add_response('put_object',{'ETag':'"probe-etag"'},put)
                        if failure=='delete_denied':
                            stub.add_client_error('delete_object','AccessDenied',http_status_code=403,expected_params={**probe,'IfMatch':ANY})
                        else:
                            stub.add_client_error('delete_object','PreconditionFailed',http_status_code=412,expected_params={**probe,'IfMatch':ANY})
                            if failure=='read_missing':
                                stub.add_client_error('get_object','NoSuchKey',http_status_code=404,expected_params=probe)
                            else:
                                stub.add_response('get_object',{'Body':io.BytesIO(b'changed' if failure=='read_corrupt' else CANARY_CONTENT)},probe)
                            if failure=='cleanup_denied':
                                stub.add_client_error('delete_object','AccessDenied',http_status_code=403,expected_params={**probe,'IfMatch':'"probe-etag"'})
                            elif failure=='cleanup_still_present':
                                stub.add_response('delete_object',{}, {**probe,'IfMatch':'"probe-etag"'})
                                stub.add_response('head_object',{},probe)
                    with self.assertRaisesRegex(DomainError,'isolated/__orphan_gc_canary__/[0-9a-f]{32}'):
                        require_conditional_delete(store)
                    stub.assert_no_pending_responses()
