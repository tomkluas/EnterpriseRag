# 验证未知上传结果的自动对账和适配器证据核验；复用临时 worker 夹具，远端查询由模拟传输控制。
import hashlib
import json
import time
import unittest

import httpx
import test_worker as fixture
from app.v3.models import Job, Replica
from app.v3.knowledge.contracts import BackendError
from app.v3.knowledge.weknora import WeKnoraBackend
from app.v3.knowledge.dify import DifyBackend


class AutoReconciliationTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = fixture.WorkerTests.asyncSetUp
    asyncTearDown = fixture.WorkerTests.asyncTearDown

    # 先制造上传结果未知的持久状态；后续恢复必须查证已有远端文件，不能用重复上传掩盖回执丢失。
    async def freeze(self):
        received=self.domain.receive('t','admin',self.space,'policy.txt',b'policy')
        class Service(fixture.FakeKnowledgeService):
            async def submit(inner,*args):raise BackendError('timeout_unknown')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        with self.db.transaction() as s:s.get(Job,received['job_id']).next_run=0
        return received

    async def test_unproven_candidates_stay_frozen_and_never_upload_again(self):
        received=await self.freeze()
        calls=[]
        class Service(fixture.FakeKnowledgeService):
            async def submit(inner,*args):self.fail('must never resubmit')
            async def reconcile_submission(inner,*args):
                calls.append('read');raise BackendError('reconciliation_ambiguous')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        self.assertEqual(calls,['read'])
        with self.db.transaction() as s:
            job=s.get(Job,received['job_id'])
            self.assertEqual(job.state,'needs_reconciliation')
            self.assertIsNone(s.query(Replica).filter_by(version_id=received['version_id']).one().remote_id)

    async def test_recovery_keeps_original_route_and_withdrawal_only_schedules_cleanup(self):
        received=await self.freeze()
        from app.v3.models import Space
        with self.db.transaction() as s:s.get(Space,self.space).active_backend='changed-route'
        used=[]
        class Service(fixture.FakeKnowledgeService):
            async def reconcile_submission(inner,*args):
                self.domain.withdraw('t','admin',received['document_id'])
                from app.v3.knowledge.contracts import Submission
                return Submission('recovered')
        self.worker.factory=lambda instance,purpose:(used.append(instance.id) or Service())
        await self.worker.run_once()
        self.assertEqual(set(used),{self.instance})
        with self.db.transaction() as s:
            replica=s.query(Replica).filter_by(version_id=received['version_id']).one()
            self.assertEqual(replica.state,'delete_pending')
            self.assertEqual(s.get(Job,received['job_id']).state,'completed')
            self.assertIsNotNone(s.query(Job).filter_by(idempotency_key='delete-after-submit:'+replica.id).first())

    async def test_expired_reconciliation_lease_cannot_accept_remote_candidate(self):
        received=await self.freeze()
        class Service(fixture.FakeKnowledgeService):
            async def reconcile_submission(inner,*args):
                with self.db.transaction() as s:s.get(Job,received['job_id']).lease_until=time.time()-1
                from app.v3.knowledge.contracts import Submission
                return Submission('late')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertIsNone(s.query(Replica).filter_by(version_id=received['version_id']).one().remote_id)

    async def test_queued_ingest_does_not_starve_due_reconciliation(self):
        received=await self.freeze()
        self.domain.receive('t','admin',self.space,'next.txt',b'next')
        calls=[]
        class Service(fixture.FakeKnowledgeService):
            async def reconcile_submission(inner,*args):
                calls.append('reconcile');raise BackendError('reconciliation_ambiguous')
        self.worker.factory=lambda *args:Service()
        await self.worker.run_once()
        await self.worker.run_once()
        self.assertEqual(calls,['reconcile'])

    async def test_legacy_job_without_submission_proof_is_never_automatically_claimed(self):
        received=await self.freeze()
        with self.db.transaction() as s:
            job=s.get(Job,received['job_id'])
            payload=dict(job.payload);payload.pop('submission_proof')
            job.payload=payload
            attempts=job.attempts;token=job.lease_token
        def factory(*args):self.fail('legacy job must not contact backend')
        self.worker.factory=factory
        self.assertIsNone(self.domain.claim_auto_reconciliation())
        self.assertFalse(await self.worker.run_once())
        with self.db.transaction() as s:
            job=s.get(Job,received['job_id'])
            self.assertEqual((job.state,job.attempts,job.lease_token),('needs_reconciliation',attempts,token))
            self.assertIsNone(s.query(Replica).filter_by(version_id=received['version_id']).one().remote_id)

    async def test_worker_recovers_unique_verified_upload_without_resubmitting(self):
        received = self.domain.receive('t', 'admin', self.space, 'policy.txt', b'policy')
        uploads = []
        class Service(fixture.FakeKnowledgeService):
            async def submit(inner, collection, filename, content, binding):
                uploads.append(binding)
                raise BackendError('timeout_unknown')
            async def reconcile_submission(inner, collection, proof):
                from app.v3.knowledge.contracts import Submission
                self.assertEqual(proof['sha256'], hashlib.sha256(b'policy').hexdigest())
                return Submission('recovered')
        self.worker.factory = lambda *args: Service()
        await self.worker.run_once()
        with self.db.transaction() as s:
            job = s.get(Job, received['job_id'])
            self.assertEqual(job.state, 'needs_reconciliation')
            self.assertIn('submission_proof', job.payload)
            job.next_run = 0
        purposes=[]
        def factory(instance,purpose):
            purposes.append(purpose)
            return Service()
        self.worker.factory=factory
        await self.worker.run_once()
        self.assertEqual(purposes,['manage'])
        await self.worker.run_once()
        with self.db.transaction() as s:
            self.assertEqual(s.get(Job, received['job_id']).state, 'completed')
            self.assertEqual(s.query(Replica).filter_by(version_id=received['version_id']).one().remote_id, 'recovered')
        self.assertEqual(len(uploads), 1)


class AdapterReconciliationTests(unittest.IsolatedAsyncioTestCase):
    def proof(self):
        return {'binding_id': 'binding-a', 'sha256': hashlib.sha256(b'policy').hexdigest()}

    async def test_weknora_unique_binding_and_original_hash_are_both_required(self):
        for content in (b'policy', b'changed'):
            def handler(request):
                if request.url.path.endswith('/download'):
                    return httpx.Response(200, content=content)
                row = {'id': 'remote-a', 'knowledge_base_id': 'kb', 'metadata': {'v3_binding_id': 'binding-a'}}
                if request.url.path.endswith('/knowledge'):
                    return httpx.Response(200, json={'success': True, 'data': [row], 'total': 1})
                return httpx.Response(200, json={'data': row})
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                backend = WeKnoraBackend('https://kb.test', 'secret', client)
                if content == b'policy':
                    self.assertEqual((await backend.reconcile_submission('kb', self.proof())).remote_id, 'remote-a')
                else:
                    with self.assertRaises(BackendError): await backend.reconcile_submission('kb', self.proof())

    async def test_weknora_duplicate_binding_is_never_claimed(self):
        rows = [{'id': key, 'knowledge_base_id': 'kb', 'metadata': {'v3_binding_id': 'binding-a'}} for key in ('a', 'b')]
        async with httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={
            'success': True, 'data': rows, 'total': 2}))) as client:
            with self.assertRaises(BackendError):
                await WeKnoraBackend('https://kb.test', 'secret', client).reconcile_submission('kb', self.proof())

    async def test_dify_post_upload_metadata_is_not_ownership_proof(self):
        def handler(request):
            return httpx.Response(200, json={'data': [{'id':'old', 'name':'policy.txt',
                'doc_metadata':{'v3_binding_id':'binding-a'}}], 'has_more':False})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(BackendError) as error:
                await DifyBackend('https://kb.test','secret',client).reconcile_submission('kb', self.proof())
            self.assertEqual(error.exception.code, 'reconciliation_unprovable')

    async def test_dify_recovers_marker_and_original_without_inventing_batch(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a-123.txt','size':6}
        def handler(request):
            if request.url.path=='/original':
                self.assertNotIn('authorization',request.headers)
                return httpx.Response(200,content=b'policy')
            if request.url.path.endswith('/download'):
                return httpx.Response(200,json={'url':'https://kb.test/original'})
            if request.url.path.endswith('/documents'):
                return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
            return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],
                'data_source_type':'upload_file','indexing_status':'completed','segment_count':3})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            backend=DifyBackend('https://kb.test','secret',client)
            submission=await backend.reconcile_submission('kb',proof)
            self.assertEqual(submission.remote_id,'remote-a')
            self.assertEqual(submission.batch_id,'')
            from app.v3.knowledge.contracts import Binding
            status=await backend.status(Binding('t','d','v','g','i','kb','remote-a','binding-a'))
            self.assertEqual((status.state,status.chunks),('completed',3))

    async def test_dify_untrusted_download_origin_is_never_contacted(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a.txt'}
        calls=[]
        def handler(request):
            calls.append(str(request.url))
            self.assertEqual(request.url.host,'kb.test')
            if request.url.path.endswith('/download'):
                return httpx.Response(200,json={'url':'https://untrusted.test/original'})
            if request.url.path.endswith('/documents'):
                return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
            return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],'data_source_type':'upload_file'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            with self.assertRaises(BackendError) as error:
                await DifyBackend('https://kb.test','secret',client).reconcile_submission('kb',proof)
            self.assertEqual(error.exception.code,'reconciliation_original_unavailable')

    # 原件下载链接属于独立信任边界；断言共享客户端的默认认证不会附带到下载请求。
    async def test_dify_original_strips_client_credentials_and_disables_default_auth(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a.txt','size':6}
        auth_paths=[];original_requests=[]
        class DefaultAuth(httpx.Auth):
            def auth_flow(inner,request):
                auth_paths.append(request.url.path)
                request.headers['Authorization']='Bearer default-auth-secret'
                request.headers['Proxy-Authorization']='Basic default-proxy-secret'
                yield request
        def handler(request):
            if request.url.path=='/original':
                original_requests.append(request)
                for header in ('authorization','cookie','proxy-authorization'):
                    self.assertNotIn(header,request.headers)
                return httpx.Response(200,content=b'policy')
            if request.url.path.endswith('/download'):
                return httpx.Response(200,json={'url':'https://kb.test/original'})
            if request.url.path.endswith('/documents'):
                return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
            return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],'data_source_type':'upload_file'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),auth=DefaultAuth(),
                headers={'Authorization':'Bearer default-header-secret','Proxy-Authorization':'Basic proxy-header-secret',
                    'Cookie':'header-session=secret'},cookies={'jar-session':'secret'}) as client:
            submission=await DifyBackend('https://kb.test','api-secret',client).reconcile_submission('kb',proof)
        self.assertEqual(submission.remote_id,'remote-a')
        self.assertEqual(len(original_requests),1)
        self.assertNotIn('/original',auth_paths)
        self.assertEqual(len(auth_paths),3)

    async def test_dify_original_redirect_is_not_followed_even_when_client_enables_redirects(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a.txt'}
        requested=[]
        def handler(request):
            requested.append(str(request.url))
            self.assertNotEqual(request.url.path,'/redirect-target')
            if request.url.path=='/original':
                return httpx.Response(302,headers={'Location':'https://kb.test/redirect-target'})
            if request.url.path.endswith('/download'):
                return httpx.Response(200,json={'url':'https://kb.test/original'})
            if request.url.path.endswith('/documents'):
                return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
            return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],'data_source_type':'upload_file'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),follow_redirects=True) as client:
            with self.assertRaises(BackendError) as error:
                await DifyBackend('https://kb.test','api-secret',client).reconcile_submission('kb',proof)
        self.assertEqual(error.exception.code,'reconciliation_original_unavailable')
        self.assertEqual(requested[-1],'https://kb.test/original')
        self.assertEqual(len(requested),4)

    async def test_dify_root_relative_signed_original_is_resolved_to_same_origin(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a.txt','size':6}
        originals=[]
        def handler(request):
            if request.url.path=='/original':
                originals.append(request)
                self.assertEqual(request.url.host,'kb.test')
                self.assertNotIn('authorization',request.headers)
                return httpx.Response(200,content=b'policy')
            if request.url.path.endswith('/download'):
                return httpx.Response(200,json={'url':'/original?signature=synthetic'})
            if request.url.path.endswith('/documents'):
                return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
            return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],'data_source_type':'upload_file'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result=await DifyBackend('https://kb.test','secret',client).reconcile_submission('kb',proof)
        self.assertEqual(result.remote_id,'remote-a')
        self.assertEqual(len(originals),1)

    async def test_dify_signed_url_unsafe_forms_have_stable_safe_reasons(self):
        proof={**self.proof(),'upload_filename':'v3-binding-a.txt'}
        for signed,reason in [('//outside.test/original','untrusted_download_origin'),
                ('https://user:password@kb.test/original','invalid_download_url'),
                ('/original#fragment','invalid_download_url'),('/original#','invalid_download_url'),
                ('/\\outside.test/original','invalid_download_url')]:
            with self.subTest(reason=reason,signed=signed):
                def handler(request):
                    if request.url.path.endswith('/download'):return httpx.Response(200,json={'url':signed})
                    if request.url.path.endswith('/documents'):
                        return httpx.Response(200,json={'data':[{'id':'remote-a','name':proof['upload_filename']}],'has_more':False})
                    self.assertNotEqual(request.url.path,'/original')
                    return httpx.Response(200,json={'id':'remote-a','name':proof['upload_filename'],'data_source_type':'upload_file'})
                async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                    with self.assertRaises(BackendError) as error:
                        await DifyBackend('https://kb.test','secret',client).reconcile_submission('kb',proof)
                self.assertEqual(error.exception.code,'reconciliation_original_unavailable')
                self.assertEqual(error.exception.reason,reason)
