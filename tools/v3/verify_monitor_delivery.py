# 验证真实回环 HTTP 告警重试，数据放入既有合成试点库的新 schema；会启动接收线程并在结束删除本轮 schema。
"""Real loopback HTTP alert delivery with synthetic data in an isolated PG schema."""
import json
import os
import tempfile
import threading
import uuid
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from datetime import datetime,timezone
from sqlalchemy import text
from sqlalchemy.engine import make_url
from dotenv import dotenv_values
from app.v3.db import Database
from app.v3.domain import Domain
from app.v3.models import Job
from app.v3.monitoring import monitor_once,ProcessHeartbeat


# 接收端第一次返回 503、之后 204；同一事件重试应保持 ID，恢复事件应换 ID，已成功投递不能再次发送。
def main():
    url=make_url(dotenv_values('v3.local')['V3_DATABASE_URL'])
    assert (url.host,url.port,url.database)==('127.0.0.1',18433,'enterpriserag_v3')
    name=json.loads(Path('docs/testing/v3/live-pilot/weknora-local-pilot.json').read_text(encoding='utf8'))['test_database']
    assert name.startswith('v3_live_')
    url=url.set(database=name);base=Database(url.render_as_string(hide_password=False))
    suffix=uuid.uuid4().hex;schema='monitor_'+suffix;tenant='monitor-'+suffix;target=None
    events=[];result={'started_at':datetime.now(timezone.utc).isoformat(),'scope':'synthetic isolated PG schema and loopback HTTP receiver only','checks':[]}
    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self):
            event=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
            assert set(event)=={'tenant_id','code','state','event_id','changed_at'}
            events.append(event)
            self.send_response(503 if len(events)==1 else 204);self.end_headers()
        def log_message(self,*args):pass
    server=ThreadingHTTPServer(('127.0.0.1',0),Receiver);thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    try:
        with base.engine.begin() as connection:connection.execute(text('CREATE SCHEMA "'+schema+'"'))
        target=Database(url.update_query_dict({'options':'-csearch_path='+schema}).render_as_string(hide_password=False));target.migrate()
        with tempfile.TemporaryDirectory() as temp:
            domain=Domain(target,Path(temp));domain.bootstrap(tenant,'admin')
            ProcessHeartbeat(target).pulse()
            space=domain.create_space(tenant,'admin','synthetic')
            record=domain.receive(tenant,'admin',space,'a.txt',b'synthetic')
            with target.transaction(tenant) as s:s.get(Job,record['job_id']).state='needs_reconciliation'
            webhook=f'http://127.0.0.1:{server.server_port}'
            monitor_once(domain,tenant,'admin',webhook_url=webhook,retry_seconds=0)
            monitor_once(domain,tenant,'admin',webhook_url=webhook,retry_seconds=0)
            assert len(events)==2 and events[0]['event_id']==events[1]['event_id']
            result['checks'].append({'name':'503-retried-and-204-delivered','status':'passed'})
            monitor_once(domain,tenant,'admin',webhook_url=webhook,retry_seconds=0);assert len(events)==2
            result['checks'].append({'name':'delivered-event-not-resent','status':'passed'})
            with target.transaction(tenant) as s:s.get(Job,record['job_id']).state='completed'
            monitor_once(domain,tenant,'admin',webhook_url=webhook,retry_seconds=0)
            assert len(events)==3 and events[-1]['state']=='resolved' and events[-1]['event_id']!=events[0]['event_id']
            result['checks'].append({'name':'resolved-event-delivered','status':'passed'})
            result['passed']=True
    finally:
        server.shutdown();server.server_close();thread.join(3)
        if target:target.engine.dispose()
        assert schema=='monitor_'+suffix and len(suffix)==32
        with base.engine.begin() as connection:connection.execute(text('DROP SCHEMA IF EXISTS "'+schema+'" CASCADE'))
        base.engine.dispose();result['finished_at']=datetime.now(timezone.utc).isoformat()
        Path('docs/testing/v3/monitor-delivery-2026-10-31.json').write_text(json.dumps(result,indent=2),encoding='utf8')
    print('LOCAL_MONITOR_DELIVERY_PASSED',len(result['checks']))


if __name__=='__main__':main()
