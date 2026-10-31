"""Deterministic HTTP model fixture for protocol tests, never a production model.

It proves API plumbing and authorization with synthetic documents, not retrieval quality.
"""
import argparse
import hashlib
import json
import math
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    def log_message(self,*args): pass

    def do_GET(self):
        self.respond({'data':[{'id':'v3-fixture-embedding','object':'model'}]})

    def do_POST(self):
        body=json.loads(self.rfile.read(int(self.headers.get('content-length','0'))))
        if self.path.rstrip('/').endswith('embeddings'):
            inputs=body.get('input',[])
            if isinstance(inputs,str):inputs=[inputs]
            vectors=[]
            for i,text in enumerate(inputs):
                values=[0.0]*64
                for token in str(text).lower().split():
                    digest=hashlib.sha256(token.encode()).digest();values[digest[0]%64]+=1
                norm=math.sqrt(sum(x*x for x in values)) or 1
                vectors.append({'index':i,'object':'embedding','embedding':[x/norm for x in values]})
            self.respond({'object':'list','model':'v3-fixture-embedding','data':vectors,'usage':{'prompt_tokens':len(inputs),'total_tokens':len(inputs)}})
        elif self.path.rstrip('/').endswith('rerank'):
            documents=body.get('documents',[])
            self.respond({'results':[{'index':i,'relevance_score':1/(i+1)} for i in range(len(documents))]})
        else:
            answer='这是协议测试替身生成的内容，不代表真实模型质量。[1]'
            if body.get('stream'):
                self.send_response(200);self.send_header('Content-Type','text/event-stream');self.end_headers()
                self.wfile.write(('data: '+json.dumps({'choices':[{'delta':{'content':answer}}]},ensure_ascii=False)+'\n\ndata: [DONE]\n\n').encode())
            else:self.respond({'choices':[{'message':{'role':'assistant','content':answer},'finish_reason':'stop'}],'usage':{'prompt_tokens':1,'completion_tokens':1,'total_tokens':2}})

    def respond(self,data):
        encoded=json.dumps(data).encode();self.send_response(200);self.send_header('Content-Type','application/json')
        self.send_header('Content-Length',str(len(encoded)));self.end_headers();self.wfile.write(encoded)


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--port',type=int,default=19100);args=parser.parse_args()
    ThreadingHTTPServer(('0.0.0.0',args.port),Handler).serve_forever()
