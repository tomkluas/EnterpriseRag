# 运行监控：按租户保存有限阶段耗时、独立进程心跳和告警投递状态，供运维快照读取。
# webhook 至少一次投递，远端已收而本地未记账时可能重发，接收方按 event_id 去重。
"""持久化运行指标并限制保留范围，载荷仅包含固定阶段及告警标识。

告警通知至少投递一次，接收方必须通过 event_id 去重。
远端接收成功后若本进程崩溃，同一事件可能再次发送。
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


# 分钟桶只累计固定阶段与结果的耗时；调用前须释放业务共享锁，避免锁升级等待。
def record_duration(db,tenant,stage,seconds,outcome='ok'):
    if stage not in STAGES or outcome not in OUTCOMES or not math.isfinite(seconds) or seconds<0:
        raise ValueError('invalid operational metric')
    bucket=int(time.time()//60)*60
    # 必须等业务事务释放租户共享锁后再调用，避免持读锁时尝试写入。
    with db.transaction(tenant) as s:
        metric=s.get(MetricBucket,(tenant,stage,outcome,bucket))
        # 相同租户、阶段、结果和分钟合并累加，只存计数与耗时，不保存问答正文。
        if metric is None:
            metric=MetricBucket(tenant_id=tenant,stage=stage,outcome=outcome,bucket=bucket,count=0,total_seconds=0,max_seconds=0)
            s.add(metric)
        metric.count+=1;metric.total_seconds+=seconds;metric.max_seconds=max(metric.max_seconds,seconds)


# 请求内缓冲样本，关闭流后统一写入；不依赖任务局部变量，兼容跨任务关闭生成器。
@asynccontextmanager
async def capture_request(db,tenant):
    # 流的推进与关闭可能由不同异步任务执行，采集器随生成器保存，不依赖任务局部变量。
    samples=[];started=time.perf_counter();outcome='error'
    try:
        yield samples
        outcome='ok'
    except (asyncio.CancelledError,GeneratorExit):
        outcome='cancelled';raise
    finally:
        samples.append(('request',time.perf_counter()-started,outcome))
        # 生成器可能被另一任务关闭，样本随生成器保存；写库必须等内部业务事务全部退出。
        def flush():
            try:
                for stage,seconds,state in samples:record_duration(db,tenant,stage,seconds,state)
            except Exception:
                logger.warning('v3_metrics_write_failed')
        await asyncio.to_thread(flush)


# 成功、异常和取消均记录结果，单调时钟计时不受系统时间调整影响。
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


# 进程心跳独立于任务租约，空闲或运行长任务时仍能证明执行器存活。
class ProcessHeartbeat:
    """独立记录进程存活状态，覆盖空闲执行器及长时间运行的任务。"""
    def __init__(self,db,worker_id=None,*,interval=15):
        if interval<=0:raise ValueError('heartbeat interval must be positive')
        self.db=db;self.worker_id=worker_id or uuid.uuid4().hex
        if len(self.worker_id)>64:raise ValueError('worker id too long')
        self.interval=interval;self.started_at=time.time();self.task=None

    def pulse(self):
        # 每次读取最新租户列表，空闲执行器也为新建租户留下存活证据。
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
        # 退出只停止更新，不删除最后时间戳，让监控在宽限期后判断执行器失联。
        self.task.cancel();await asyncio.gather(self.task,return_exceptions=True)


async def run_worker(worker,*,poll_seconds=1,heartbeat_seconds=15):
    if poll_seconds<=0:raise ValueError('worker poll interval must be positive')
    async with ProcessHeartbeat(worker.db,interval=heartbeat_seconds):
        while True:
            worked=await worker.run_once()
            if not worked:await asyncio.sleep(poll_seconds)


# 读取管理员快照后按告警变化建立事件；先落投递租约，再在事务外调用 webhook。
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
        # 先在事务内领取投递租约，外部 HTTP 在提交后执行，避免等待 webhook 时持业务锁。
        existing={row.code:row for row in s.scalars(select(AlertState).where(AlertState.tenant_id==tenant))}
        for code in active|set(existing):
            row=existing.get(code);enabled=code in active
            if row is None:
                row=AlertState(tenant_id=tenant,code=code,active=enabled,event_id=uuid.uuid4().hex,
                    changed_at=now,delivery='pending' if webhook_url else 'disabled',attempts=0,next_attempt=0,lease_until=0,delivered_at=0,error='')
                s.add(row)
            # 触发与恢复各生成新事件；状态未变时失败重发沿用原 ID，便于接收方去重。
            elif row.active!=enabled:
                row.active=enabled;row.event_id=uuid.uuid4().hex;row.changed_at=now
                row.delivery='pending' if webhook_url else 'disabled';row.attempts=0
                row.next_attempt=0;row.lease_until=0;row.delivered_at=0;row.error=''
            elif webhook_url and row.delivery=='disabled':row.delivery='pending'
            if webhook_url and row.delivery!='delivered' and row.next_attempt<=now and row.lease_until<=now:
                row.lease_until=now+timeout_seconds+30;row.attempts+=1;row.delivery='sending'
                deliver.append({'tenant_id':tenant,'code':code,'state':'firing' if enabled else 'resolved',
                    'event_id':row.event_id,'changed_at':row.changed_at})
        # 运行指标只保留七天；进程心跳记录继续保留，便于发现空闲执行器失联。
        s.execute(delete(MetricBucket).where(MetricBucket.tenant_id==tenant,MetricBucket.bucket<int(now-7*86400)))
    for event in deliver:
        # 至少一次投递：远端已接收但本地未落结果时可能重发，接收方按 event_id 去重。
        sent=False
        try:
            with httpx.Client(timeout=timeout_seconds,follow_redirects=False,trust_env=False) as client:
                response=client.post(webhook_url,json=event,headers={'Idempotency-Key':event['event_id']})
                sent=200<=response.status_code<300
        except Exception:pass
        with domain.db.transaction(tenant) as s:
            row=s.get(AlertState,(tenant,event['code']))
            # 只更新本次事件的结果，避免旧请求晚返回覆盖已经变化的新告警。
            if row and row.event_id==event['event_id']:
                row.lease_until=0;row.delivery='delivered' if sent else 'retrying'
                row.delivered_at=time.time() if sent else 0
                row.next_attempt=0 if sent else time.time()+min(3600,retry_seconds*2**min(row.attempts-1,7))
                # 只持久化固定失败标签，不把 webhook 返回正文或可能含秘密的异常写入数据库。
                row.error='' if sent else 'webhook_delivery_failed'
    return snapshot(domain,tenant,user)


async def run_monitor(domain,tenant,user,*,webhook_url='',interval=15):
    if interval<=0:raise ValueError('monitor interval must be positive')
    while True:
        try:await asyncio.to_thread(monitor_once,domain,tenant,user,webhook_url=webhook_url)
        except Exception:logger.warning('v3_monitor_poll_failed')
        await asyncio.sleep(interval)
