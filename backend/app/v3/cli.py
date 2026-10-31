import argparse
import asyncio
import json
import os
from pathlib import Path
from dotenv import load_dotenv
import httpx
from .config import Settings
from .db import Database
from .domain import Domain
from .knowledge.registry import BackendRegistry
from .jobs.worker import Worker


def main():
    parser=argparse.ArgumentParser(description='EnterpriseRag V3 管理命令')
    parser.add_argument('--env-file',required=True,help='独立 V3 配置文件；不读取旧 .env')
    sub=parser.add_subparsers(dest='command',required=True)
    sub.add_parser('migrate');sub.add_parser('worker')
    sub.add_parser('operations-status')
    sub.add_parser('check-configuration')
    monitor=sub.add_parser('monitor');monitor.add_argument('--once',action='store_true')
    plan_gc=sub.add_parser('plan-orphan-gc');plan_gc.add_argument('--output',required=True)
    plan_gc.add_argument('--retention-days',type=float,default=7)
    apply_gc=sub.add_parser('apply-orphan-gc');apply_gc.add_argument('--plan',required=True)
    apply_gc.add_argument('--confirm-delete',action='store_true',required=True)
    backup=sub.add_parser('backup-bundle');backup.add_argument('--output',required=True)
    restore=sub.add_parser('restore-bundle');restore.add_argument('--input',required=True)
    restore.add_argument('--confirm-empty-target',action='store_true',required=True)
    accept=sub.add_parser('accept-validation');accept.add_argument('--instance',required=True);accept.add_argument('--report',required=True)
    inspect=sub.add_parser('inspect-reconciliation');inspect.add_argument('--job',required=True)
    reconcile=sub.add_parser('reconcile-job');reconcile.add_argument('--job',required=True);reconcile.add_argument('--report',required=True)
    args=parser.parse_args();load_dotenv(args.env_file,override=False)
    settings=Settings.from_env()
    if args.command=='check-configuration':
        from .preflight import check_settings
        report=check_settings(settings)
        print(json.dumps(report,ensure_ascii=False,indent=2))
        if not report['passed']:raise SystemExit(1)
        return
    if args.command=='worker' and settings.mode!='development':
        from .preflight import check_settings
        if not check_settings(settings)['passed']:
            raise RuntimeError('生产 Worker 配置不完整，请按 docs/v3/production-configuration.md 修复')
    db=Database(settings.database_url)
    if args.command=='restore-bundle':
        from sqlalchemy import select
        from .models import Tenant
        from .recovery import restore_tenant
        db.migrate()
        with db.transaction() as session:
            if session.execute(select(Tenant).limit(1)).first():
                raise ValueError('CLI 恢复仅允许全新空数据库，不覆盖已有租户')
        print(json.dumps(restore_tenant(db,settings.tenant_id,args.input,settings.objects_dir),ensure_ascii=False))
        return
    domain=Domain(db,settings.objects_dir,development=settings.mode=='development')
    if args.command=='migrate':
        db.migrate();domain.bootstrap(settings.tenant_id,settings.bootstrap_user)
        print('V3 数据库初始化完成，旧数据未导入。')
    elif args.command=='backup-bundle':
        from .recovery import backup_tenant
        with db.transaction(settings.tenant_id,shared=True) as session:
            domain.admin(session,settings.tenant_id,settings.bootstrap_user)
        manifest=backup_tenant(db,domain.objects,settings.tenant_id,args.output)
        print(json.dumps({'tenant_id':settings.tenant_id,'objects':len(manifest['objects']),'created':True},ensure_ascii=False))
    elif args.command=='accept-validation':
        with open(args.report,encoding='utf8') as stream:report=json.load(stream)
        domain.accept_validation(settings.tenant_id,settings.bootstrap_user,args.instance,report)
        print('指定实例的运行验证报告已签收。')
    elif args.command=='plan-orphan-gc':
        from .orphan_gc import plan_orphans
        report=plan_orphans(domain,settings.tenant_id,settings.bootstrap_user,args.retention_days)
        with Path(args.output).open('x',encoding='utf8') as stream:json.dump(report,stream,ensure_ascii=False,indent=2)
        print(json.dumps({'planned':len(report['candidates']),'deleted':0},ensure_ascii=False))
    elif args.command=='apply-orphan-gc':
        from .orphan_gc import apply_orphan_plan
        with open(args.plan,encoding='utf8') as stream:plan=json.load(stream)
        result=apply_orphan_plan(domain,settings.tenant_id,settings.bootstrap_user,plan)
        print(json.dumps(result,ensure_ascii=False))
        if result['failed']:raise SystemExit(1)
    elif args.command=='monitor':
        from .monitoring import monitor_once,run_monitor
        webhook=os.getenv('V3_ALERT_WEBHOOK_URL','')
        if args.once:print(json.dumps(monitor_once(domain,settings.tenant_id,settings.bootstrap_user,webhook_url=webhook),ensure_ascii=False,indent=2))
        else:asyncio.run(run_monitor(domain,settings.tenant_id,settings.bootstrap_user,webhook_url=webhook))
    elif args.command=='operations-status':
        from .operations import snapshot
        print(json.dumps(snapshot(domain,settings.tenant_id,settings.bootstrap_user),ensure_ascii=False,indent=2))
    elif args.command=='inspect-reconciliation':
        print(json.dumps(domain.reconciliation_snapshot(settings.tenant_id,settings.bootstrap_user,args.job),ensure_ascii=False,indent=2))
    elif args.command=='reconcile-job':
        with open(args.report,encoding='utf8') as stream:report=json.load(stream)
        domain.reconcile_job(settings.tenant_id,settings.bootstrap_user,args.job,report)
        print('对账回执已记录；未直接发布或标记索引就绪。')
    else:
        async def work():
            from .monitoring import ProcessHeartbeat,run_monitor
            async with httpx.AsyncClient() as client:
                registry=BackendRegistry(client);worker=Worker(domain,registry.get)
                async def jobs():
                    while True:
                        try:
                            if not await worker.run_once():await asyncio.sleep(2)
                        except Exception:
                            # Database outages must not end the worker supervisor loop.
                            import logging
                            logging.getLogger(__name__).warning('v3_worker_poll_failed')
                            await asyncio.sleep(2)
                async with ProcessHeartbeat(db):
                    async with asyncio.TaskGroup() as group:
                        group.create_task(jobs())
                        group.create_task(run_monitor(domain,settings.tenant_id,settings.bootstrap_user,
                            webhook_url=os.getenv('V3_ALERT_WEBHOOK_URL','')))
        asyncio.run(work())

if __name__=='__main__':main()
