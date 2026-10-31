import asyncio
import json
import tempfile
import threading
import time
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from sqlalchemy import text
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import Job
from app.v3.operations import snapshot


class MonitoringTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.url='sqlite:///'+str(Path(self.temp.name)/'monitor.db')
        self.db=Database(self.url,testing=True);self.db.migrate()
        self.domain=Domain(self.db,Path(self.temp.name)/'objects');self.domain.bootstrap('t','admin')

    def tearDown(self):
        self.db.engine.dispose();self.temp.cleanup()

    def test_creation_age_is_independent_of_retry_schedule_and_unknown_stays_unknown(self):
        with self.db.transaction('t') as s:
            s.add(Job(id='old',tenant_id='t',owner='admin',kind='ingest',idempotency_key='old',payload={},created_at=time.time()-700,next_run=time.time()+100))
            s.add(Job(id='legacy',tenant_id='t',owner='admin',kind='ingest',idempotency_key='legacy',payload={},created_at=0,next_run=0))
        result=snapshot(self.domain,'t','admin')
        self.assertGreaterEqual(result['oldest_queued_seconds'],699)
        self.assertEqual(result['queued_without_age'],1)
        self.assertIn('overdue_queue',result['alerts'])

    def test_existing_database_migrates_job_age_to_unknown(self):
        with self.db.transaction('t') as s:
            s.add(Job(id='legacy',tenant_id='t',owner='admin',kind='ingest',idempotency_key='legacy',payload={}))
        with self.db.engine.begin() as c:
            c.execute(text('ALTER TABLE v3_jobs DROP COLUMN created_at'))
        self.db.migrate();self.db.migrate()
        with self.db.engine.connect() as c:
            self.assertIn('created_at',[row[1] for row in c.execute(text('PRAGMA table_info(v3_jobs)'))])
            self.assertEqual(c.scalar(text("SELECT created_at FROM v3_jobs WHERE id='legacy'")),0)

    def test_idle_process_heartbeat_and_restart_persistent_metrics(self):
        from app.v3.monitoring import ProcessHeartbeat, record_duration
        async def run():
            async with ProcessHeartbeat(self.db,'worker-test',interval=.02):
                await asyncio.sleep(.07)
                result=snapshot(self.domain,'t','admin')
                self.assertEqual(result['workers']['healthy'],1)
                self.assertEqual(result['workers']['stale'],0)
        asyncio.run(run())
        record_duration(self.db,'t','retrieve',.5,'ok')
        second=Database(self.url,testing=True)
        try:
            record_duration(second,'t','retrieve',.7,'error')
            result=snapshot(Domain(second,Path(self.temp.name)/'objects'),'t','admin')
            self.assertEqual(sum(v['count'] for v in result['durations']),2)
            self.assertAlmostEqual(sum(v['total_seconds'] for v in result['durations']),1.2)
            with self.assertRaises(ValueError):record_duration(second,'t','https://secret.test/?key=x',1,'ok')
        finally:second.engine.dispose()

    def test_idle_worker_loss_is_visible_and_metrics_are_tenant_scoped(self):
        from app.v3.monitoring import ProcessHeartbeat, record_duration
        from app.v3.models import WorkerPulse
        ProcessHeartbeat(self.db,'gone').pulse()
        with self.db.transaction('t') as s:s.get(WorkerPulse,('t','gone')).last_seen=time.time()-70
        record_duration(self.db,'other','request',19)
        result=snapshot(self.domain,'t','admin')
        self.assertEqual(result['durations'],[])
        self.assertEqual(result['workers']['stale'],1)
        self.assertIn('worker_heartbeat_missing',result['alerts'])

    def test_monitor_requires_admin_and_does_not_disclose_webhook_errors(self):
        from app.v3.monitoring import monitor_once
        from app.v3.domain import DomainError
        self.domain.add_user('t','admin','reader',role='reader')
        with self.assertRaises(DomainError):monitor_once(self.domain,'t','reader')
        with self.assertRaises(ValueError):monitor_once(self.domain,'t','admin',webhook_url='https://password:secret@127.0.0.1/')

    def test_request_cancellation_closes_shared_business_scope_before_metric_write(self):
        from contextlib import asynccontextmanager
        from unittest.mock import patch
        from app.v3.chat.service import ChatService
        import app.v3.monitoring as monitoring
        held=False;original=monitoring.record_duration
        @asynccontextmanager
        async def business_scope():
            nonlocal held
            held=True
            try:yield
            finally:held=False
        async def response(*args):
            async with business_scope():yield {'event':'token','data':'secret text'}
        def record(*args):
            self.assertFalse(held)
            return original(*args)
        chat=ChatService(self.domain,None,None);chat._answer=response
        async def cancel():
            stream=chat.answer('t','admin','c','s','secret query')
            await anext(stream);await stream.aclose()
        with patch.object(monitoring,'record_duration',side_effect=record):asyncio.run(cancel())
        metrics=snapshot(self.domain,'t','admin')['durations']
        self.assertEqual(metrics[0]['outcome'],'cancelled')
        self.assertNotIn('secret',json.dumps(metrics))

    def test_local_webhook_retries_deduplicates_and_delivers_recovery(self):
        from app.v3.monitoring import monitor_once
        calls=[]
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
                self.send_response(503 if len(calls)==1 else 204);self.end_headers()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Receiver)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            with self.db.transaction('t') as s:
                s.add(Job(id='bad',tenant_id='t',owner='admin',kind='ingest',idempotency_key='bad',payload={},state='failed'))
            url='http://127.0.0.1:'+str(server.server_port)+'/synthetic'
            monitor_once(self.domain,'t','admin')
            self.assertEqual(calls,[])
            monitor_once(self.domain,'t','admin',webhook_url=url,retry_seconds=0)
            self.assertEqual(snapshot(self.domain,'t','admin')['alert_delivery'][0]['delivery'],'retrying')
            monitor_once(self.domain,'t','admin',webhook_url=url,retry_seconds=0)
            monitor_once(self.domain,'t','admin',webhook_url=url,retry_seconds=0)
            self.assertEqual(len(calls),2)
            self.assertEqual(calls[0]['event_id'],calls[1]['event_id'])
            with self.db.transaction('t') as s:s.get(Job,'bad').state='completed'
            monitor_once(self.domain,'t','admin',webhook_url=url,retry_seconds=0)
            self.assertEqual(calls[-1]['state'],'resolved')
            self.assertNotEqual(calls[-1]['event_id'],calls[0]['event_id'])
            monitor_once(self.domain,'t','admin',webhook_url=url,retry_seconds=0)
            self.assertEqual(len(calls),3)
            result=snapshot(self.domain,'t','admin')
            self.assertEqual(result['alert_delivery'][0]['delivery'],'delivered')
        finally:server.shutdown();server.server_close();thread.join()

    def test_simultaneous_monitors_claim_one_delivery_without_holding_business_lock(self):
        from app.v3.monitoring import monitor_once
        entered=threading.Event();release=threading.Event();calls=[]
        class Receiver(BaseHTTPRequestHandler):
            def do_POST(self):
                calls.append(self.rfile.read(int(self.headers['Content-Length'])))
                entered.set();release.wait(3)
                self.send_response(204);self.end_headers()
            def log_message(self,*args):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Receiver)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        with self.db.transaction('t') as s:
            s.add(Job(id='bad',tenant_id='t',owner='admin',kind='ingest',idempotency_key='bad',payload={},state='failed'))
        url='http://127.0.0.1:'+str(server.server_port)+'/synthetic'
        errors=[]
        def send():
            try:monitor_once(self.domain,'t','admin',webhook_url=url)
            except Exception as error:errors.append(type(error).__name__)
        sender=threading.Thread(target=send);sender.start()
        try:
            self.assertTrue(entered.wait(3))
            result=monitor_once(self.domain,'t','admin',webhook_url=url)
            self.assertEqual(result['alert_delivery'][0]['delivery'],'sending')
            self.assertEqual(len(calls),1)
            self.assertEqual(errors,[])
        finally:
            release.set();sender.join(5)
            server.shutdown();server.server_close();thread.join()
        self.assertEqual(errors,[])
