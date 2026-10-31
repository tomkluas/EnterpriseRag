"""Run real-model, dual-backend text acceptance before enabling a local-only pilot.

Never for production: full G0 remains separate. All prototype DB writes use a new
database. Platform uploads contain synthetic markers only; secrets stay private.
"""
import argparse,asyncio,json,os,time,uuid,sys,unittest,subprocess
from pathlib import Path
from sqlalchemy import create_engine,select
from sqlalchemy.engine import make_url
from dotenv import dotenv_values
import httpx
from app.v3.config import Settings
from app.v3.db import Database
from app.v3.domain import Domain,DomainError,uid
from app.v3.models import BackendInstance,Replica,Space,Job,Version
from app.v3.knowledge.registry import BackendRegistry
from app.v3.knowledge.contracts import Scope,Binding,BackendError
from app.v3.jobs.worker import Worker
from app.v3.chat.models import ModelService
from app.v3.chat.service import ChatService
from app.v3.qualification import matching_format_checks


async def main(args):
    config=dotenv_values(args.env_file)
    for key,value in config.items():
        if value is not None:os.environ[key]=value
    settings=Settings.from_env()
    if settings.mode!='development':raise SystemExit('Local pilot is forbidden outside development mode')
    root=Path(args.runtime_dir);enrollment=root/'v3-enrollment.json'
    state=json.loads(enrollment.read_text()) if enrollment.exists() else {}
    runtime=Domain(Database(settings.database_url),settings.objects_dir,development=True)
    def save():enrollment.write_text(json.dumps(state,ensure_ascii=False,indent=2))
    for provider,prefix in [('weknora','WK'),('dify','DIFY')]:
        if provider not in state:
            state[provider]=runtime.register_backend(settings.tenant_id,settings.bootstrap_user,provider,
                config['V3_'+prefix+'_BASE_URL'],'V3_SECRET_'+prefix+'_READ','V3_SECRET_'+prefix+'_WRITE',
                'V3_SECRET_'+prefix+'_READ',{'collection_id':config['V3_'+prefix+'_COLLECTION_ID']});save()
    url=make_url(settings.database_url)
    if (url.host,url.port,url.database)!=('127.0.0.1',18433,'enterpriserag_v3'):raise SystemExit('Expected isolated local V3 deployment')
    test_name='v3_live_'+uuid.uuid4().hex
    engine=create_engine(url,isolation_level='AUTOCOMMIT')
    with engine.connect() as connection:connection.exec_driver_sql('CREATE DATABASE '+test_name)
    engine.dispose();test_url=url.set(database=test_name).render_as_string(hide_password=False)
    db=Database(test_url);db.migrate();domain=Domain(db,root/test_name);domain.bootstrap('live','admin');domain.add_user('live','admin','qa','reader')
    instances={}
    for provider in ['weknora','dify']:
        with runtime.db.transaction(settings.tenant_id,shared=True) as session:source=session.get(BackendInstance,state[provider])
        if source.base_url!={'weknora':'http://127.0.0.1:18080','dify':'http://127.0.0.1:18082'}[provider]:
            raise RuntimeError('Local container attestation cannot qualify a remote or different endpoint')
        with db.transaction('live') as session:
            # Prototype fixture only in the new isolated DB, never the runtime qualification.
            instance=BackendInstance(id=source.id,tenant_id='live',provider=source.provider,base_url=source.base_url,
                key_ref=source.key_ref,write_key_ref=source.write_key_ref,manage_key_ref=source.manage_key_ref,
                collection_config=source.collection_config,verified=True,capabilities=['segment']);session.add(instance)
        instances[provider]=instance
    checks=[];events=[];forbidden='FORBIDDEN_'+uuid.uuid4().hex;run=uuid.uuid4().hex
    model_hosts={make_url(value).host for value in [settings.model_base_url,settings.rerank_base_url] if value}
    async def capture(request):
        if request.url.host in model_hosts:
            if forbidden.encode() in request.content:raise AssertionError('Forbidden evidence reached model')
            events.append({'purpose':'rerank' if 'rerank' in request.url.path else 'chat','path':request.url.path})
    async with httpx.AsyncClient(event_hooks={'request':[capture]}) as client:
        registry=BackendRegistry(client);worker=Worker(domain,registry.get);models=ModelService(settings,client);chat=ChatService(domain,registry,models)
        async def drain(version_id,instance_id):
            async with asyncio.timeout(180):
                while True:
                    await worker.run_once()
                    with db.transaction('live',shared=True) as session:
                        replica=session.scalar(select(Replica).filter_by(version_id=version_id,instance_id=instance_id,state='ready'))
                        if replica:return
                    await asyncio.sleep(1)
        async def ask(space,expected):
            conv=domain.create_conversation('live','qa');answer='';evidence=[]
            async for event in chat.answer('live','qa',conv,space,'本次合成制度的差旅报销标准是多少元？请给出明确数字和资料引用。'):
                if event['event']=='token':answer+=event['data']
                if event['event']=='sources':evidence=event['data']
            assert str(expected) in answer and evidence and forbidden not in answer
            print('Real answer passed',expected,'sources',len(evidence),flush=True)
            return conv
        space=domain.create_space('live','admin','synthetic-live-'+run)
        first=domain.prepare_switch('live','admin',space,instances['weknora'].id);domain.activate_switch('live','admin',first,0)
        a=domain.receive('live','admin',space,'allowed-'+run+'.txt',f'本次合成制度 {run}：差旅报销标准为每天138元。policy standard 138。'.encode())
        b=domain.receive('live','admin',space,'forbidden-'+run+'.txt',f'{forbidden} 差旅报销标准 差旅报销标准 差旅报销标准 policy standard 9999元。'.encode())
        for record in [a,b]:await drain(record['version_id'],instances['weknora'].id);domain.publish('live','admin',record['version_id'])
        domain.grant('live','admin',a['document_id'],['qa'],[])
        conv=await ask(space,138)
        # Actual separate vector / keyword paths with positive controls in each backend.
        for provider,instance in instances.items():
            if provider=='dify':
                task=domain.prepare_switch('live','admin',space,instance.id)
                for record in [a,b]:await drain(record['version_id'],instance.id)
                domain.activate_switch('live','admin',task,1)
            scope=domain.scope('live','qa',space);backend=registry.get(instance,'retrieve');binding=scope.bindings[0]
            for label in (['vector','keyword','hybrid'] if provider=='weknora' else ['semantic_search','full_text_search']):
                if provider=='weknora':
                    data=await backend.request('POST',f'/api/v1/knowledge-bases/{binding.collection_id}/hybrid-search',json={
                        'query_text':'差旅报销标准 policy standard','knowledge_ids':[binding.remote_id],'match_count':10,
                        'disable_vector_match':label=='keyword','disable_keywords_match':label=='vector','skip_context_enrichment':True})
                    rows=data.get('data',[]);assert rows and all(row['knowledge_id']==binding.remote_id for row in rows)
                else:
                    data=await backend.request('POST',f'/v1/datasets/{binding.collection_id}/retrieve',json={'query':'差旅报销标准 policy standard',
                        'retrieval_model':{'search_method':label,'reranking_enable':False,'top_k':10,'score_threshold_enabled':False,
                        'metadata_filtering_conditions':{'logical_operator':'and','conditions':[{'name':'v3_binding_id','comparison_operator':'in','value':[binding.id]}]}}})
                    rows=data.get('records',[]);assert rows and all(row['segment']['document_id']==binding.remote_id for row in rows)
                print(provider,label,'positive scope passed',flush=True)
            # Credential is deliberately denied access to the older diagnostic collection.
            old=json.loads((root/('weknora-fixture-kb.json' if provider=='weknora' else 'dify-test-auth.json')).read_text(encoding='utf-8-sig'))
            old_id=old['data']['id'] if provider=='weknora' else old['dataset']['id']
            path=f'/api/v1/knowledge-bases/{old_id}' if provider=='weknora' else f'/v1/datasets/{old_id}'
            try:await backend.request('GET',path);raise AssertionError('Cross-collection key scope failed')
            except BackendError as error:assert error.code=='permission_denied'
            if provider=='weknora':
                try:await backend.submit(binding.collection_id,'denied.txt',b'must not be accepted','read-key-denied');raise AssertionError('Read key wrote a file')
                except BackendError as error:assert error.code=='permission_denied'
            if provider=='dify':await ask(space,138)
        # New draft while Dify is active, then prepare back to WeKnora before publication.
        updated=domain.receive('live','admin',space,'updated-'+run+'.md',f'本次合成制度新版 {run}：差旅报销标准为每天246元。policy standard 246。'.encode(),document_id=a['document_id'])
        await drain(updated['version_id'],instances['dify'].id)
        await ask(space,138)  # Draft has not replaced the current pointer.
        back=domain.prepare_switch('live','admin',space,instances['weknora'].id)
        await drain(updated['version_id'],instances['weknora'].id);domain.activate_switch('live','admin',back,2)
        domain.publish('live','admin',updated['version_id']);await ask(space,246)
        # Fault-inject a known terminal parse failure locally, then exercise real
        # platform copies and model answers through the cancellation recovery path.
        broken=domain.receive('live','admin',space,'broken-'+run+'.docx',b'synthetic invalid docx',document_id=a['document_id'])
        with db.transaction('live') as session:
            session.get(Job,broken['job_id']).state='failed'
            session.get(Version,broken['version_id']).state='failed'
        stale=domain.prepare_switch('live','admin',space,instances['dify'].id)
        domain.cancel_version('live','admin',broken['document_id'],broken['version_id'])
        try:domain.activate_switch('live','admin',stale,3);raise AssertionError('Cancelled draft did not invalidate switch')
        except DomainError:pass
        fresh=domain.prepare_switch('live','admin',space,instances['dify'].id)
        await drain(updated['version_id'],instances['dify'].id)
        domain.activate_switch('live','admin',fresh,3);await ask(space,246)
        restored=domain.prepare_switch('live','admin',space,instances['weknora'].id)
        domain.activate_switch('live','admin',restored,4);await ask(space,246)
        print('Cancelled failed draft, current version preservation and real roundtrip passed',flush=True)
        domain.grant('live','admin',a['document_id'],[],['qa'])
        assert domain.history('live','qa',conv)[0]['state']=='restricted'
        try:domain.read_original('live','qa',a['document_id']);raise AssertionError('Revoked original remained accessible')
        except DomainError:pass
        assert not domain.scope('live','qa',space).bindings
        # Empty scope must not dispatch upstream at all, for both real adapters.
        for instance in instances.values():assert await registry.get(instance).retrieve('empty',Scope('live',()))==[]
        assert {'rerank','chat'}.issubset({event['purpose'] for event in events}),'Both rerank and chat dispatch must be observed'
        print('Roundtrip, draft publication, model boundary and revocation passed',flush=True)
    # Run actual contract, unknown-result, lease, historical dependency and PG concurrency suite.
    os.environ['V3_TEST_DATABASE_URL']=test_url
    suite=unittest.defaultTestLoader.discover('backend/tests/v3')
    result=await asyncio.to_thread(unittest.TextTestRunner(verbosity=0).run,suite)
    if not result.wasSuccessful() or result.skipped:raise RuntimeError('Acceptance regression suite did not fully pass')
    meanings=['real-model-interfaces','positive-filtered-recall','credential-scope','forbidden-not-sent-to-model',
        'real-roundtrip-and-draft-publish','empty-and-invalid-scope-contracts','revocation-and-history','durable-jobs-and-concurrency']
    checks=[{'id':f'LOCAL-{i:02}','status':'passed','name':name} for i,name in enumerate(meanings,1)]
    Path(args.output).mkdir(parents=True,exist_ok=True)
    for provider,instance in instances.items():
        container={'weknora':'WeKnora-app','dify':'enterpriserag-v3-dify-g0-api-1'}[provider]
        digest=subprocess.run(['docker','inspect',container,'--format','{{.Image}}'],capture_output=True,text=True,check=True).stdout.strip()
        if not digest.startswith('sha256:') or len(digest)!=71:raise RuntimeError('Unable to bind acceptance to running container image')
        expected_digest={'weknora':'sha256:25b20959b7456f7842ef55ed4feff3694dbd0c6f8af4117c1fb3c5dec6bdf14d',
            'dify':'sha256:5a372d832406893b0508982f1011cd1c18ca379d74730dce004391ac70d186d1'}[provider]
        if digest!=expected_digest:raise RuntimeError('Running platform image differs from the reviewed deployment; re-review before enabling')
        format_path=Path(args.output)/'format-probes.json'
        format_rows=json.loads(format_path.read_text()).get('checks',[]) if format_path.exists() else []
        format_checks=matching_format_checks(format_rows,provider=provider,endpoint=instance.base_url,
            collection_id=instance.collection_config['collection_id'],image_digest=digest,embedding_model=config['V3_EMBEDDING_MODEL'])
        formats={'.txt','.md'}|{row['extension'] for row in format_checks if row.get('provider')==provider and row.get('status')=='passed' and row.get('fact_preserved') is True}
        report={'profile':'local_text_pilot','audit_level':'runtime_local_pilot','instance_id':instance.id,'provider':provider,
            'commit':{'weknora':'f17ee8a8da901bd658a4cb25f40b5fd41adbfdd7','dify':'50abea97062110044f15d0e03ccecadd9ccc87ca'}[provider],
            'image_digest':digest,'approved_by':'local deployment under explicit user authorization','allowed_formats':sorted(formats),'format_checks':format_checks,
            'capabilities':['segment'],'checks':checks,'test_database':test_name,'regression_tests':result.testsRun,
            'model_mode':'real_cloud_models','production_g0':'not_complete','configuration':{'question_generation':provider=='weknora',
            'dify_compat_patch':provider=='dify'},'observed_model_dispatches':{purpose:sum(event['purpose']==purpose for event in events) for purpose in ['rerank','chat']},
            'not_verified':['scanned PDF OCR','complex/cross-page tables','exact page/cell alignment','production capacity','full recovery','full G0']}
        runtime.accept_validation(settings.tenant_id,settings.bootstrap_user,instance.id,report)
        (Path(args.output)/(provider+'-local-pilot.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    if 'space' not in state:state['space']=runtime.create_space(settings.tenant_id,settings.bootstrap_user,'V3 企业知识库');save()
    with runtime.db.transaction(settings.tenant_id,shared=True) as session:space_row=session.get(Space,state['space'])
    if not space_row.active_backend:
        switch=runtime.prepare_switch(settings.tenant_id,settings.bootstrap_user,state['space'],state['weknora'])
        runtime.activate_switch(settings.tenant_id,settings.bootstrap_user,switch,space_row.route_revision)
    print('LOCAL_TEXT_PILOT_ENABLED_BOTH_BACKENDS',flush=True)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--env-file',required=True);parser.add_argument('--runtime-dir',required=True);parser.add_argument('--output',required=True)
    asyncio.run(main(parser.parse_args()))
