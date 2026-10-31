import unittest
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import patch
import httpx
import json
from app.v3.chat.models import ModelService
from app.v3.config import Settings


class ModelDispatchTests(unittest.IsolatedAsyncioTestCase):
    async def test_qianwen_native_rerank_uses_nested_request_and_response(self):
        def handler(request):
            self.assertEqual(str(request.url),'https://maas.qianwenaiapi.com/api/v1/services/rerank/text-rerank/text-rerank')
            body=json.loads(request.content)
            self.assertEqual(body['input']['documents'],['a','b'])
            self.assertEqual(body['parameters']['top_n'],2)
            return httpx.Response(200,json={'output':{'results':[{'index':1,'relevance_score':.9},{'index':0,'relevance_score':.1}]}})
        settings=Settings(model_base_url='https://maas.qianwenaiapi.com/compatible-mode/v1',rerank_model='qwen3.7-text-rerank')
        with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                ranked=await ModelService(settings,client).rank('q',[SimpleNamespace(content='a'),SimpleNamespace(content='b')])
        self.assertEqual(ranked[0].content,'b')

    async def test_qianwen_compatible_rerank_uses_plural_endpoint(self):
        def handler(request):
            self.assertEqual(request.url.path,'/compatible-api/v1/reranks')
            return httpx.Response(200,json={'results':[{'index':0,'relevance_score':.8}]})
        settings=Settings(model_base_url='https://maas.qianwenaiapi.com/compatible-mode/v1',rerank_model='qwen3-rerank')
        with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                await ModelService(settings,client).rank('q',[SimpleNamespace(content='a')])

    async def test_dispatch_gate_is_released_before_model_body_is_consumed(self):
        held=False
        @asynccontextmanager
        async def gate():
            nonlocal held
            held=True
            try:yield
            finally:held=False
        outer=self
        class Body(httpx.AsyncByteStream):
            async def __aiter__(self):
                outer.assertFalse(held,'正文等待不能阻塞撤权')
                yield b'data: {"choices":[{"delta":{"content":"answer [1]"}}]}\n\ndata: [DONE]\n\n'
        def handler(request):
            self.assertTrue(held,'派发时需要持有共享协调锁')
            return httpx.Response(200,stream=Body())
        settings=Settings(model_base_url='https://model.test/v1',chat_model='test')
        with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                result=[part async for part in ModelService(settings,client).stream('q',[SimpleNamespace(content='evidence')],[],dispatch_gate=gate)]
        self.assertEqual(result,['answer [1]'])

    async def test_rewrite_validates_length_and_semantics(self):
        from app.v3.domain import DomainError
        responses=[{'query':'退款期限','faithful':True},{'faithful':True},{'query':'x'*251,'faithful':True},{'query':'退款','faithful':False}]
        def handler(request):
            body=json.loads(request.content)
            self.assertFalse(body['stream'])
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(responses.pop(0))}}]})
        settings=Settings(model_base_url='https://model.test/v1',chat_model='test')
        with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                model=ModelService(settings,client)
                self.assertEqual(await model.rewrite('长问题'*100,[]),'退款期限')
                for _ in range(2):
                    with self.assertRaises(DomainError):await model.rewrite('长问题'*100,[])

    async def test_rewrite_independent_semantic_check_can_reject_candidate(self):
        from app.v3.domain import DomainError
        responses=[{'query':'退款期限','faithful':True},{'faithful':False}]
        def handler(request):
            return httpx.Response(200,json={'choices':[{'message':{'content':json.dumps(responses.pop(0))}}]})
        settings=Settings(model_base_url='https://model.test/v1',chat_model='test')
        with patch.dict('os.environ',{'V3_SECRET_MODEL':'fixture'}):
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                with self.assertRaises(DomainError):await ModelService(settings,client).rewrite('退款期限和适用例外',[])
