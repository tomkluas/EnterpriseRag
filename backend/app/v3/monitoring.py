"""Durable bounded operational telemetry. Payloads contain only fixed stage/alert labels.

Webhook delivery is at least once: receivers must deduplicate using event_id. A
crash after remote acceptance can cause the same event to be sent again.
"""
import asyncio
import logging
import math
import time
import uuid
from contextlib import asynccontextmanager
from sqlalchemy import select, delete
import httpx
from .models import Tenant, WorkerPulse, MetricBucket, AlertState

STAGES=frozenset({'request','rewrite','retrieve','rerank','generate','worker'})
OUTCOMES=frozenset({'ok','error','cancelled'})
logger=logging.getLogger(__name__)


def record_duration(db,tenant,stage,seconds,outcome='ok'):
    if stage not in STAGES or outcome not in OUTCOMES or not math.isfinite(seconds) or seconds<0:
        raise ValueError('invalid operational metric')
    bucket=int(time.time()//60)*60
    # Call only after business transactions release their shared tenant lock.
    with db.transaction(tenant) as s:
        metric=s.get(MetricBucket,(tenant,stage,outcome,bucket))
        if metric is None:
            metric=MetricBucket(tenant_id=tenant,stage=stage,outcome=outcome,bucket=bucket,count=0,total_seconds=0,max_seconds=0)
            s.add(metric)
        metric.count+=1;metric.total_seconds+=seconds;metric.max_seconds=max(metric.max_seconds,seconds)


@asynccontextmanager
async def capture_request(db,tenant):
    # The stream may be advanced and closed by different asyncio Tasks. Keep
    # its collector on the generator, rather than in task-local ContextVars.
    samples=[];started=time.perf_counter();outcome='error'
    try:
        yield samples
        outcome='ok'
    except (asyncio.CancelledError,GeneratorExit):
        outcome='cancelled';raise
    finally:
        samples.append(('request',time.perf_counter()-started,outcome))
        # Buffer while dispatch gates/stream yields hold shared locks; flush only
        # after closing the inner generator, outside every business transaction.
        def flush():
            try:
                for stage,seconds,state in samples:record_duration(db,tenant,stage,seconds,state)
            except Exception:
                logger.warning('v3_metrics_write_failed')
        await asyncio.to_thread(flush)


@asynccontextmanager
async def stage_timer(stage,samples):
    if stage not in STAGES:raise ValueError('invalid operational stage')
    started=time.perf_counter();outcome='error'
    try:
        yield
        outcome='ok'
    except (asyncio.CancelledError,GeneratorExit):
        outcome='cancelled';raise
    finally:
        samples.append((stage,time.perf_counter()-started,outcome))


async def timed_call(stage,samples,call,*args,**kwargs):
    async with stage_timer(stage,samples):return await call(*args,**kwargs)


class ProcessHeartbeat:
    """Independent process liveness, including idle workers and long-running jobs."""
    def __init__(self,db,worker_id=None,*,interval=15):
        if interval<=0:raise ValueError('heartbeat interval must be positive')
        self.db=db;self.worker_id=worker_id or uuid.uuid4().hex
        if len(self.worker_id)>64:raise ValueError('worker id too long')
        self.interval=interval;self.started_at=time.time();self.task=None

    def pulse(self):
        # Refresh tenants on each pulse: a new tenant receives idle-worker health.
        with self.db.transaction(shared=True) as s:tenants=list(s.scalars(select(Tenant.id)))
        for tenant in tenants:
            with self.db.transaction(tenant) as s:
                row=s.get(WorkerPulse,(tenant,self.worker_id))
                if row is None:
                    s.add(WorkerPulse(tenant_id=tenant,worker_id=self.worker_id,last_seen=time.time(),started_at=self.started_at))
                else:row.last_seen=time.time()

    async def _run(self):
        while True:
            await asyncio.sleep(self.interval)
            try:await asyncio.to_thread(self.pulse)
            except Exception:logger.warning('v3_worker_pulse_failed')

    async def __aenter__(self):
        await asyncio.to_thread(self.pulse)
        self.task=asyncio.create_task(self._run())
        return self

    async def __aexit__(self,*args):
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)
        # Leave the last timestamp: polling detects exits after the grace period.


async def run_worker(worker,*,poll_seconds=1,heartbeat_seconds=15):
    if poll_seconds<=0:raise ValueError('worker poll interval must be positive')
    async with ProcessHeartbeat(worker.db,interval=heartbeat_seconds):
        while True:
            worked=await worker.run_once()
            if not worked:await asyncio.sleep(poll_seconds)


def monitor_once(domain,tenant,user,*,webhook_url='',retry_seconds=30,timeout_seconds=5):
    from .operations import snapshot
    if retry_seconds<0 or timeout_seconds<=0:raise ValueError('invalid alert timing')
    if webhook_url:
        parsed=httpx.URL(webhook_url)
        if parsed.scheme not in ('http','https') or not parsed.host or parsed.userinfo:
            raise ValueError('webhook must be an HTTP URL without embedded credentials')
    result=snapshot(domain,tenant,user);now=time.time();active=set(result['alerts']);deliver=[]
    with domain.db.transaction(tenant) as s:
        domain.admin(s,tenant,user)
        existing={row.code:row for row in s.scalars(select(AlertState).where(AlertState.tenant_id==tenant))}
        for code in active|set(existing):
            row=existing.get(code);enabled=code in active
            if row is None:
                row=AlertState(tenant_id=tenant,code=code,active=enabled,event_id=uuid.uuid4().hex,
                    changed_at=now,delivery='pending' if webhook_url else 'disabled',attempts=0,next_attempt=0,lease_until=0,delivered_at=0,error='')
                s.add(row)
            elif row.active!=enabled:
                row.active=enabled;row.event_id=uuid.uuid4().hex;row.changed_at=now
                row.delivery='pending' if webhook_url else 'disabled';row.attempts=0
                row.next_attempt=0;row.lease_until=0;row.delivered_at=0;row.error=''
            elif webhook_url and row.delivery=='disabled':row.delivery='pending'
            if webhook_url and row.delivery!='delivered' and row.next_attempt<=now and row.lease_until<=now:
                row.lease_until=now+timeout_seconds+30;row.attempts+=1;row.delivery='sending'
                deliver.append({'tenant_id':tenant,'code':code,'state':'firing' if enabled else 'resolved',
                    'event_id':row.event_id,'changed_at':row.changed_at})
        # Telemetry has bounded retention; health rows are retained long enough
        # for a lost idle worker to be observable and are safe after recovery.
        s.execute(delete(MetricBucket).where(MetricBucket.tenant_id==tenant,MetricBucket.bucket<int(now-7*86400)))
    for event in deliver:
        sent=False
        try:
            with httpx.Client(timeout=timeout_seconds,follow_redirects=False,trust_env=False) as client:
                response=client.post(webhook_url,json=event,headers={'Idempotency-Key':event['event_id']})
                sent=200<=response.status_code<300
        except Exception:pass
        with domain.db.transaction(tenant) as s:
            row=s.get(AlertState,(tenant,event['code']))
            if row and row.event_id==event['event_id']:
                row.lease_until=0;row.delivery='delivered' if sent else 'retrying'
                row.delivered_at=time.time() if sent else 0
                row.next_attempt=0 if sent else time.time()+min(3600,retry_seconds*2**min(row.attempts-1,7))
                row.error='' if sent else 'webhook_delivery_failed'
    return snapshot(domain,tenant,user)


async def run_monitor(domain,tenant,user,*,webhook_url='',interval=15):
    if interval<=0:raise ValueError('monitor interval must be positive')
    while True:
        try:await asyncio.to_thread(monitor_once,domain,tenant,user,webhook_url=webhook_url)
        except Exception:logger.warning('v3_monitor_poll_failed')
        await asyncio.sleep(interval)
