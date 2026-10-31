"""Real lost-receipt probe in a fresh schema of the existing synthetic pilot DB.

Never changes platform keys, runtime qualification, existing tenant routing, or data.
Only the wrapper loses a real successful upload receipt; recovery uses real APIs.
"""
import argparse
import asyncio
import json
import os
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

import httpx
from dotenv import dotenv_values
from sqlalchemy import select, text
from sqlalchemy.engine import make_url

from app.v3.db import Database
from app.v3.domain import Domain, DomainError, dump
from app.v3.jobs.worker import Worker
from app.v3.knowledge.contracts import BackendError, Binding
from app.v3.knowledge.registry import BackendRegistry
from app.v3.models import BackendInstance, Job, Replica, Space


def stage(value):
    print(json.dumps({'stage':value},ensure_ascii=False),flush=True)


def normalize_check(check,deadline_exceeded=False):
    # Older probe snapshots captured the durable state before adding explicit
    # receipt/readiness metrics. backend_processing after the injected unknown
    # outcome can only be reached through the worker's verified receipt path.
    check['remote_receipt_recovered']=bool(check.get('reconciliation')) or (
        check.get('initial_job_state')=='needs_reconciliation' and check.get('submission_proof_saved') is True
        and check.get('replica_state') in ('backend_processing','ready'))
    check['index_ready']=check.get('replica_state')=='ready' and check.get('chunks',0)>0 and check.get('job_state')=='completed'
    if deadline_exceeded and check.get('status') in ('not_run','running'):
        check['status']='timeout'
    return check


async def run(args):
    started=time.monotonic()
    config=dotenv_values(args.env_file)
    url=make_url(config.get('V3_DATABASE_URL',''))
    if (url.host,url.port,url.database)!=('127.0.0.1',18433,'enterpriserag_v3'):
        raise ValueError('Expected isolated local V3 deployment')
    pilot=json.loads(Path(args.pilot_report).read_text(encoding='utf-8-sig'))
    database=pilot['test_database']
    if not isinstance(database,str) or not database.startswith('v3_live_'):
        raise ValueError('Expected existing synthetic pilot database')
    for key,value in config.items():
        if value is not None:os.environ[key]=value
    test_url=url.set(database=database)
    source_db=Database(test_url.render_as_string(hide_password=False))
    schema='v3_reconcile_'+uuid.uuid4().hex
    tenant='reconcile_'+uuid.uuid4().hex
    report={'at':datetime.now(timezone.utc).isoformat(),'audit_level':'isolated_real_fault_injection',
        'test_database':database,'schema':schema,'tenant':tenant,'checks':[],
        'production_qualification_changed':False,'remote_authorization_changed':False}
    previous=None
    if args.append_report:
        previous=json.loads(Path(args.output).read_text(encoding='utf8'))
        if previous.get('test_database')!=database or (not args.allow_retest and any(c['provider'] in args.providers for c in previous['checks'])):
            raise ValueError('Append is only allowed for previously unattempted providers in the same pilot DB')
        report['previous_runs']=[previous]
    scoped_db=None;schema_created=False;receipts=[];instances=[]
    try:
        stage('read_existing_pilot_backend_registrations')
        with source_db.transaction() as s:
            s.execute(text('SET TRANSACTION READ ONLY'))
            sources=[dump(row) for row in s.scalars(select(BackendInstance).where(BackendInstance.tenant_id=='live'))]
        sources=[row for row in sources if row['provider'] in ('weknora','dify')]
        if {row['provider'] for row in sources}!={'weknora','dify'} or len(sources)!=2:
            raise ValueError('Expected exactly two pilot registrations')
        for row in sources:
            if row['base_url']!={'weknora':'http://127.0.0.1:18080','dify':'http://127.0.0.1:18082'}[row['provider']]:
                raise ValueError('Expected local pinned pilot endpoints')
        stage('create_fresh_schema_and_temporary_originals')
        with source_db.engine.begin() as connection:
            connection.exec_driver_sql('CREATE SCHEMA '+schema)
        schema_created=True
        scoped_url=test_url.update_query_dict({'options':'-csearch_path='+schema})
        scoped_db=Database(scoped_url.render_as_string(hide_password=False));scoped_db.migrate()
        with tempfile.TemporaryDirectory(prefix='v3-auto-reconciliation-') as objects:
            domain=Domain(scoped_db,objects,development=True);domain.bootstrap(tenant,'probe-admin')
            for row in sources:
                # Preserve ID, report/profile and verified status. Only tenant moves
                # into this probe's namespace; no qualification is manufactured.
                row={**row,'tenant_id':tenant}
                with scoped_db.transaction(tenant) as s:
                    instance=BackendInstance(**row);s.add(instance)
                instances.append(instance)
            async with httpx.AsyncClient() as client:
                registry=BackendRegistry(client)
                try:
                    async with asyncio.timeout(args.timeout):
                        for instance in instances:
                            if instance.provider not in args.providers:continue
                            check={'provider':instance.provider,'instance_id':instance.id,'submit_calls':0,
                                'upload_receipts':[],'status':'running','recovered':False}
                            report['checks'].append(check)
                            real_write=registry.get(instance,'write')
                            class LoseReceipt:
                                def __getattr__(self,name):return getattr(real_write,name)
                                async def submit(self,collection,filename,content,binding_id):
                                    check['submit_calls']+=1
                                    if check['submit_calls']!=1:raise BackendError('probe_forbidden_repeat_upload')
                                    submission=await real_write.submit(collection,filename,content,binding_id)
                                    binding=Binding(tenant,'probe','probe',binding_id,instance.id,collection,
                                        submission.remote_id,binding_id,submission.batch_id)
                                    receipts.append((instance,binding))
                                    check['upload_receipts'].append({'remote_id':submission.remote_id,'batch_id':submission.batch_id})
                                    stage(instance.provider+':real_upload_returned_receipt_then_simulated_timeout')
                                    raise BackendError('timeout_unknown')
                            wrapped=LoseReceipt()
                            def factory(target,purpose):
                                if purpose=='write' and target.id==instance.id:return wrapped
                                backend=registry.get(target,purpose)
                                if purpose!='manage':return backend
                                class CaptureReason:
                                    def __getattr__(self,name):return getattr(backend,name)
                                    async def reconcile_submission(self,*values):
                                        try:return await backend.reconcile_submission(*values)
                                        except BackendError as error:
                                            check['reconciliation_error_reason']=error.reason
                                            raise
                                return CaptureReason()
                            worker=Worker(domain,factory)
                            space=domain.create_space(tenant,'probe-admin','Synthetic lost-receipt '+uuid.uuid4().hex)
                            with scoped_db.transaction(tenant) as s:s.get(Space,space).active_backend=instance.id
                            received=domain.receive(tenant,'probe-admin',space,'synthetic-'+uuid.uuid4().hex+'.txt',
                                ('SYNTHETIC isolated receipt recovery '+uuid.uuid4().hex+'. Travel policy allowance is 317 yuan.').encode())
                            stage(instance.provider+':worker_real_upload')
                            await worker.run_once()
                            with scoped_db.transaction(tenant) as s:
                                job=s.get(Job,received['job_id'])
                                check['initial_job_state']=job.state
                                check['initial_error_code']=job.error
                                check['submission_proof_saved']=bool(job.payload.get('submission_proof'))
                            stage(instance.provider+':automatic_remote_discovery_and_original_hash')
                            last_stage=time.monotonic()
                            while True:
                                await worker.run_once()
                                with scoped_db.transaction(tenant) as s:
                                    job=s.get(Job,received['job_id'])
                                    replica=s.scalar(select(Replica).where(Replica.version_id==received['version_id']))
                                    check.update(job_state=job.state,error_code=job.error,
                                        replica_state=replica.state if replica else None,chunks=replica.chunks if replica else 0)
                                    check['reconciliation']=job.payload.get('automatic_reconciliation')
                                    check['receipt_recovered']=bool(check['reconciliation'])
                                    normalize_check(check)
                                    if args.receipt_only and check['remote_receipt_recovered']:
                                        check['status']='receipt_recovered';break
                                    if time.monotonic()-last_stage>=10:
                                        stage(instance.provider+':'+str(check['replica_state'])+':'+job.state)
                                        last_stage=time.monotonic()
                                    if replica and replica.state=='ready' and job.state=='completed':
                                        check['recovered']=True
                                        check['reconciliation']=job.payload.get('automatic_reconciliation')
                                        check['status']='passed' if check['submit_calls']==1 and check['reconciliation'] else 'failed'
                                        break
                                    if job.state=='needs_reconciliation' and job.error not in ('timeout_unknown','needs_reconciliation'):
                                        check['status']='blocked';break
                                    if job.state=='failed':check['status']='failed';break
                                await asyncio.sleep(.5)
                            stage(instance.provider+':'+check['status'])
                except TimeoutError:
                    report['probe_error_code']='probe_deadline_exceeded'
                    if report['checks'] and report['checks'][-1]['status']=='running':
                        report['checks'][-1]['status']='timeout'
                except BackendError as error:
                    report['probe_error_code']=error.code
                except DomainError:
                    report['probe_error_code']='domain_rejected_probe'
                finally:
                    stage('cleanup_only_known_upload_receipts')
                    for instance,binding in receipts:
                        cleanup={'provider':instance.provider,'remote_id':binding.remote_id,'confirmed':False}
                        report.setdefault('cleanup',[]).append(cleanup)
                        try:
                            async with asyncio.timeout(15):
                                backend=registry.get(instance,'write')
                                try:await backend.delete(binding)
                                except BackendError as error:
                                    if error.code!='not_found':raise
                                reader=registry.get(instance,'manage')
                                while not cleanup['confirmed']:
                                    cleanup['confirmed']=await reader.deletion_confirmed(binding)
                                    if not cleanup['confirmed']:await asyncio.sleep(.5)
                        except BackendError as error:cleanup['error_code']=error.code
                        except TimeoutError:cleanup['error_code']='cleanup_deadline_exceeded'
    except BackendError as error:report['probe_error_code']=error.code
    except Exception:
        # Never include raw exceptions: upstream URLs may carry signed credentials.
        report['probe_error_code']='probe_setup_or_internal_error'
    finally:
        if scoped_db:scoped_db.engine.dispose()
        if schema_created:
            stage('drop_only_this_probe_schema')
            try:
                with source_db.engine.begin() as connection:
                    connection.exec_driver_sql('DROP SCHEMA '+schema+' CASCADE')
                report['schema_removed']=True
            except Exception:report['schema_cleanup_error_code']='schema_drop_failed'
        source_db.engine.dispose()
        report['elapsed_seconds']=round(time.monotonic()-started,3)
        for check in report['checks']:
            normalize_check(check,report.get('probe_error_code')=='probe_deadline_exceeded')
        report['latest_checks']=list(report['checks'])
        report['receipt_probe_passed']=len(report['checks'])==len(args.providers) and all(
            c['remote_receipt_recovered'] and c['submit_calls']==1 for c in report['checks']) and all(
            c['confirmed'] for c in report.get('cleanup',[])) and report.get('schema_removed',False) and not report.get('probe_error_code')
        if previous:
            report['checks']=[normalize_check(dict(check),previous.get('probe_error_code')=='probe_deadline_exceeded')
                for check in previous['checks']]+report['checks']
            report['cleanup']=previous.get('cleanup',[])+report.get('cleanup',[])
        report['passed']=len(report['checks'])==2 and all(c['status']=='passed' for c in report['checks']) and all(
            c['confirmed'] for c in report.get('cleanup',[])) and not report.get('probe_error_code') and report.get('schema_removed',False)
        output=Path(args.output);output.parent.mkdir(parents=True,exist_ok=True)
        output.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf8')
        stage('report_written:'+str(output))
    return report


if __name__=='__main__':
    parser=argparse.ArgumentParser()
    parser.add_argument('--env-file',default='v3.local')
    parser.add_argument('--pilot-report',default='docs/testing/v3/live-pilot/weknora-local-pilot.json')
    parser.add_argument('--output',default='docs/testing/v3/auto-reconciliation-live-2026-10-31.json')
    parser.add_argument('--timeout',type=float,default=85)
    parser.add_argument('--providers',nargs='+',choices=('weknora','dify'),default=['weknora','dify'])
    parser.add_argument('--append-report',action='store_true')
    parser.add_argument('--allow-retest',action='store_true',help='Explicitly authorized retest of a previously attempted provider')
    parser.add_argument('--receipt-only',action='store_true',help='Stop after verified receipt recovery; do not wait for index readiness')
    arguments=parser.parse_args()
    if not 30<=arguments.timeout<=90:raise SystemExit('Probe timeout must be 30..90 seconds')
    result=asyncio.run(run(arguments))
    print(json.dumps({'passed':result['passed'],'receipt_probe_passed':result['receipt_probe_passed'],
        'error_code':result.get('probe_error_code')},ensure_ascii=False))
    raise SystemExit(0 if (result['receipt_probe_passed'] if arguments.receipt_only else result['passed']) else 1)
