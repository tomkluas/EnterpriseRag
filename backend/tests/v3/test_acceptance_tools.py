# 验证验收报告和 SSE 流解析的门槛；HTTP 使用 MockTransport，输入流与临时报告均由测试构造，不连接模型服务。
import importlib.util
import json
import unittest
from pathlib import Path
import httpx

SPEC = importlib.util.spec_from_file_location('acceptance', Path(__file__).resolve().parents[3] / 'tools/v3/acceptance.py')
a = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(a)


class AcceptanceTests(unittest.IsolatedAsyncioTestCase):
    async def test_sse_ignores_status_heartbeat_and_empty_tokens(self):
        ticks = iter([0, 1, 2, 3, 4, 5, 6])
        async def lines():
            for value in [': heartbeat', '', 'data: {"event":"status","data":"working"}', '',
                          'data: {"event":"token","data":"  "}', '',
                          'data: {"event":"token","data":"answer"}', '',
                          'data: {"event":"done"}', '']:
                yield value
        result = await a.measure_sse(lines(), clock=lambda: next(ticks))
        self.assertTrue(result['success'])
        self.assertGreater(result['first_token_s'], 0)
        self.assertNotIn('"data"', json.dumps(result))

    async def test_incomplete_error_and_empty_are_failures(self):
        for events in [[{'event':'done'}], [{'event':'token','data':'x'}],
                       [{'event':'token','data':'x'}, {'event':'error'}, {'event':'done'}]]:
            async def lines():
                for event in events:
                    yield 'data: ' + json.dumps(event)
                    yield ''
            self.assertFalse((await a.measure_sse(lines()))['success'])

    def test_capacity_cannot_pass_missing_duration_concurrency_or_context(self):
        rows = [{'success':True,'answer_validated':True,'first_token_s':1,'complete_s':2}]*100
        good = dict(elapsed_s=1800, load_s=1800, concurrency=10, context={key:'fixed' for key in a.CONTEXT_FIELDS})
        self.assertTrue(a.capacity_report(rows, **good)['passed'])
        for change in [{'load_s':1799}, {'concurrency':9}, {'context':{}}, {'elapsed_s':1}]:
            self.assertFalse(a.capacity_report(rows, **(good | change))['passed'])
        rows[0] = {'success':False,'first_token_s':None,'complete_s':2}
        rows[1] = rows[0]
        self.assertFalse(a.capacity_report(rows, **good)['passed'])

    async def test_transport_records_timeout_without_body(self):
        async def handler(request):
            raise httpx.ReadTimeout('secret response')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='https://test') as client:
            row = await a.request_once(client, 'space', 'question', 1)
        self.assertFalse(row['success'])
        self.assertEqual(row['failure'], 'timeout')
        self.assertNotIn('secret', json.dumps(row))

    def test_quality_requires_coverage_review_and_no_leak(self):
        data = a.quality_example()
        self.assertFalse(a.quality_report(data)['passed'])
        data['documents'] = [{'id':str(i),'format':a.FORMATS[i % len(a.FORMATS)],'supported':True} for i in range(20)]
        data['cases'] = [dict(id=str(i), document_ids=[str(i%20)], category=a.CATEGORIES[i%len(a.CATEGORIES)],
            expected_answer_ref='frozen-reference', evidence_ref='frozen-source', reviewer='owner', reviewed=True,
            outcome='completed', correct=True, citation_supported=True, refusal_correct=True,
            location_correct=True, permission_leak=False) for i in range(200)]
        data.update(frozen=True, frozen_at='2026-10-30', dataset_id='business-v1', representative_confirmed_by='owner')
        self.assertTrue(a.quality_report(data)['passed'])
        data['cases'][0]['reviewed'] = False
        self.assertFalse(a.quality_report(data)['passed'])
        data['cases'][0]['reviewed'] = True
        data['cases'][7]['permission_leak'] = True
        self.assertFalse(a.quality_report(data)['passed'])
        data['cases'][7]['permission_leak'] = False
        data['documents'][0]['supported'] = False
        self.assertFalse(a.quality_report(data)['passed'])

    def test_error_is_never_correct_refusal(self):
        data = a.quality_example()
        data['cases']=[dict(id='a', category='unanswerable', outcome='error', refusal_correct=True)]
        report=a.quality_report(data)
        self.assertEqual(report['refusal_accuracy'], 0)

    async def test_mock_http_creates_session_then_streams_without_retaining_content(self):
        requests=[]
        async def handler(request):
            requests.append(request.url.path)
            if request.url.path.endswith('/conversations'):
                return httpx.Response(201,json={'id':'isolated'})
            return httpx.Response(200,headers={'content-type':'text/event-stream'},text='data: {"event":"token","data":"private-answer"}\n\ndata: {"event":"done"}\n\n')
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='https://test') as client:
            row=await a.request_once(client,'s','private-question',1)
        self.assertTrue(row['success'])
        self.assertEqual(len(requests),2)
        self.assertNotIn('private',json.dumps(row))

    def test_cli_defaults_to_no_network_and_writes_only_temporary_report(self):
        import tempfile
        with tempfile.TemporaryDirectory() as directory:
            target=Path(directory)/'report.json'
            self.assertEqual(a.main(['capacity','--output',str(target)]),2)
            self.assertTrue(json.loads(target.read_text(encoding='utf-8'))['dry_run'])

    def test_unterminated_and_malformed_frames_fail(self):
        async def run():
            for payload in [['data: {"event":"done"}'], ['data: not-json','']]:
                async def lines():
                    for line in payload: yield line
                self.assertFalse((await a.measure_sse(lines()))['success'])
        import asyncio
        asyncio.run(run())

    def test_malformed_quality_inputs_are_rejected(self):
        for data in [[], {'documents':None}, {'cases':[None]}, {'cases':[{'id':'x','document_ids':[{}]}]}]:
            with self.assertRaises(ValueError):
                a.quality_report(data)

    async def test_answerable_capacity_requires_expected_text_and_sources(self):
        async def refusal_lines():
            for line in ['data: {"event":"token","data":"无法回答"}', '', 'data: {"event":"done"}', '']:
                yield line
        smoke = await a.measure_sse(refusal_lines())
        smoke_report = a.capacity_report([smoke]*100,elapsed_s=1800,load_s=1800,concurrency=10,
                                        context={key:'fixed' for key in a.CONTEXT_FIELDS})
        self.assertFalse(smoke_report['passed'])
        async def measure(events, expected=('known fact',)):
            async def lines():
                for event in events:
                    yield 'data: ' + json.dumps(event)
                    yield ''
            return await a.measure_sse(lines(), expect_substrings=expected)
        token={'event':'token','data':'known fact'}
        done={'event':'done'}
        sources={'event':'sources','data':[{'document_id':'test'}]}
        for events in [[token,done], [sources,{'event':'token','data':'无法回答'},done],
                       [{'event':'sources','data':[]},token,done]]:
            row=await measure(events)
            self.assertFalse(row['success'])
            self.assertFalse(row['answer_validated'])
        row=await measure([sources,{'event':'token','data':'known '},{'event':'token','data':'fact'},done])
        self.assertTrue(row['success'])
        self.assertTrue(row['answer_validated'])
        self.assertNotIn('known fact',json.dumps(row))
        row=await measure([sources,token,done], ('known fact','absent'))
        self.assertFalse(row['success'])
        row=await measure([token,done], ())
        self.assertTrue(row['success'])
        self.assertFalse(row['answer_validated'])
        report=a.capacity_report([row]*100,elapsed_s=1800,load_s=1800,concurrency=10,
                                 context={key:'fixed' for key in a.CONTEXT_FIELDS})
        self.assertFalse(report['passed'])
        self.assertFalse(report['gates']['answer_validation'])

    async def test_answer_validation_forwarded_by_http_client(self):
        async def handler(request):
            if request.url.path.endswith('/conversations'):
                return httpx.Response(201,json={'id':'mock'})
            events=[{'event':'sources','data':[{'id':'test'}]}, {'event':'token','data':'known'},
                    {'event':'token','data':' '}, {'event':'token','data':'fact'}, {'event':'done'}]
            body=''.join('data: '+json.dumps(event)+'\n\n' for event in events)
            return httpx.Response(200,headers={'content-type':'text/event-stream'},text=body)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler),base_url='https://test') as client:
            matched=await a.request_once(client,'s','q',1,('known fact',))
            mismatch=await a.request_once(client,'s','q',1,('absent',))
        self.assertTrue(matched['answer_validated'])
        self.assertFalse(mismatch['success'])
        self.assertNotIn('known fact',json.dumps(matched))
