# 运维只读汇总：管理员查看本租户任务、副本、心跳及最近一天的阶段耗时。
# 输出计数和固定告警码，不包含原件、问题正文或服务凭据。
"""按租户汇总运行计数，不包含资料正文或凭据。"""
import time
from sqlalchemy import select,func
from .models import Job,Replica,WorkerPulse,MetricBucket,AlertState


# 查询限定租户并验证管理员；任务租约反映执行权，进程心跳反映执行器是否存活。
def snapshot(domain,tenant,user,*,worker_stale_seconds=60):
    now=time.time()
    with domain.db.transaction(tenant,shared=True) as s:
        domain.admin(s,tenant,user)
        jobs=dict(s.execute(select(Job.state,func.count()).where(Job.tenant_id==tenant).group_by(Job.state)).all())
        bindings=dict(s.execute(select(Replica.state,func.count()).where(Replica.tenant_id==tenant).group_by(Replica.state)).all())
        expired=s.scalar(select(func.count()).select_from(Job).where(Job.tenant_id==tenant,Job.state=='running',Job.lease_until<now))
        oldest=s.scalar(select(func.min(Job.next_run)).where(Job.tenant_id==tenant,Job.state=='queued',Job.next_run>0,Job.next_run<=now))
        created=s.scalar(select(func.min(Job.created_at)).where(Job.tenant_id==tenant,Job.state=='queued',Job.created_at>0))
        # 旧任务可能没有创建时间，单独计数而不伪造排队时长。
        unknown_age=s.scalar(select(func.count()).select_from(Job).where(Job.tenant_id==tenant,Job.state=='queued',Job.created_at==0))
        pulses=list(s.scalars(select(WorkerPulse).where(WorkerPulse.tenant_id==tenant)))
        # 心跳独立于任务领取：没有任务时执行器仍应存活，不能拿空队列证明进程健康。
        healthy=sum(p.last_seen>=now-worker_stale_seconds for p in pulses)
        stale=len(pulses)-healthy
        # 只汇总最近一天的分钟桶，按阶段和结果分类，不把失败耗时混入成功指标。
        durations=[{'stage':stage,'outcome':outcome,'count':count,'total_seconds':total,'max_seconds':maximum}
            for stage,outcome,count,total,maximum in s.execute(select(MetricBucket.stage,MetricBucket.outcome,
                func.sum(MetricBucket.count),func.sum(MetricBucket.total_seconds),func.max(MetricBucket.max_seconds))
                .where(MetricBucket.tenant_id==tenant,MetricBucket.bucket>=int((now-86400)//60)*60)
                .group_by(MetricBucket.stage,MetricBucket.outcome))]
        deliveries=[{'code':a.code,'active':a.active,'event_id':a.event_id,'changed_at':a.changed_at,
            'delivery':a.delivery,'attempts':a.attempts,'next_attempt':a.next_attempt,
            'delivered_at':a.delivered_at,'error':a.error}
            for a in s.scalars(select(AlertState).where(AlertState.tenant_id==tenant).order_by(AlertState.code))]
    # 由汇总状态推导告警；远端未知结果、删除未确认和执行器失联分别报告。
    alerts=[]
    if jobs.get('needs_reconciliation'):alerts.append('unknown_remote_results')
    if jobs.get('failed'):alerts.append('failed_jobs')
    if expired:alerts.append('expired_worker_leases')
    if (created and now-created>=300) or (oldest and now-oldest>=300):alerts.append('overdue_queue')
    if stale and not healthy:alerts.append('worker_heartbeat_missing')
    elif not healthy and created and now-created>=worker_stale_seconds:alerts.append('worker_heartbeat_missing')
    if bindings.get('delete_requested') or bindings.get('delete_pending'):alerts.append('unconfirmed_remote_deletions')
    return {'checked_at':now,'jobs':jobs,'bindings':bindings,'expired_leases':expired,
        'oldest_due_seconds':max(0,now-oldest) if oldest else None,
        'oldest_queued_seconds':max(0,now-created) if created else None,
        'queued_without_age':unknown_age,'workers':{'healthy':healthy,'stale':stale,
            'last_seen':max((p.last_seen for p in pulses),default=None),'stale_after_seconds':worker_stale_seconds},
        'durations_window_seconds':86400,'durations':durations,'alert_delivery':deliveries,'alerts':alerts}
