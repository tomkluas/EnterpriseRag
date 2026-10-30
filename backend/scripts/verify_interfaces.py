"""Live smoke checks. Creates uniquely named test resources and removes them in finally.

Run inside the backend container with PYTHONPATH=/app; only the current service's
API and configured dependencies are used. Existing defaults and business data are unchanged.
"""
import json
import os
import time
import uuid
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.core.auth import create_access_token
from app.core.config import settings
from app.db.database import SessionLocal
from app.db.models import User


def main():
    origin = os.environ.get("API_CHECK_ORIGIN", "http://nginx")
    results = []
    with SessionLocal() as db:
        user = db.query(User).filter(User.role == "admin", User.is_active.is_(True)).first()
        if user is None:
            raise RuntimeError("需要已有活跃管理员以执行诊断")
        token = create_access_token(user.id, user.role)

    def request(method, path, data=None, params=None, body=None, content_type=None):
        if params:
            path += "?" + urlencode(params)
        headers = {"Authorization": "Bearer " + token}
        if data is not None:
            body, content_type = json.dumps(data).encode(), "application/json"
        if content_type:
            headers["Content-Type"] = content_type
        with urlopen(Request(origin + path, data=body, headers=headers, method=method), timeout=180) as response:
            payload = response.read().decode()
            return payload if "text/event-stream" in response.headers.get("Content-Type", "") else json.loads(payload)

    def record(name, ok, **details):
        result = {"check": name, "passed": bool(ok), **details}
        results.append(result)
        print(json.dumps(result, ensure_ascii=False), flush=True)

    def check_stream(name, data, expected_source=None):
        payload = request("POST", "/api/v1/chat/stream", data)
        events = [json.loads(line[6:]) for line in payload.splitlines() if line.startswith("data: ")]
        answer = "".join(e["data"] for e in events if e["event"] == "token")
        errors = [e["data"] for e in events if e["event"] == "error"]
        sources = [e["data"]["source_type"] for e in events if e["event"] == "source"]
        ok = answer and not errors and any(e["event"] == "done" for e in events)
        if expected_source:
            ok = ok and expected_source in sources
        record(name, ok, answer_chars=len(answer), source_types=sources, errors=errors)
        return answer

    kb_id = model_id = doc_id = None
    try:
        for path in ("/api/health", "/api/v1/auth/me", "/api/v1/knowledge/list",
                     "/api/v1/admin/llm-configs", "/api/v1/admin/db-connections", "/api/v1/admin/users"):
            request("GET", path)
            record("GET " + path, True)
        try:
            request("POST", "/api/v1/chat/stream", {"session_id": "validation"})
            record("chat body validation", False)
        except HTTPError as error:
            payload = json.load(error)
            record("chat body validation", error.code == 422 and payload["detail"][0]["loc"] == ["body", "query"])
        if settings.deepseek_api_key:
            model = request("POST", "/api/v1/admin/llm-configs", {
                "name": "__api_check_" + uuid.uuid4().hex, "provider": "deepseek",
                "model_string": "deepseek-reasoner", "api_key": settings.deepseek_api_key,
                "base_url": settings.deepseek_api_base,
            })
            model_id = model["id"]
            request("POST", "/api/v1/admin/llm-configs/test", params={"config_id": model_id})
            record("model create / actual connection test", True)
        kb_id = request("POST", "/api/v1/knowledge/create", params={
            "name": "__api_check_" + uuid.uuid4().hex, "description": "temporary interface verification",
        })["id"]
        boundary = uuid.uuid4().hex
        document = "验收代号：API-CHECK-20261029。需求：支持创建知识库、上传文件、查询资料、显示来源和删除测试记录。"
        body = (f'--{boundary}\r\nContent-Disposition: form-data; name="file"; filename="api-check.txt"\r\n'
                f'Content-Type: text/plain\r\n\r\n{document}\r\n--{boundary}--\r\n').encode()
        doc_id = request("POST", f"/api/v1/knowledge/{kb_id}/upload", body=body,
                         content_type="multipart/form-data; boundary=" + boundary)["doc_id"]
        deadline = time.monotonic() + 150
        while True:
            documents = request("GET", f"/api/v1/knowledge/{kb_id}/documents")
            doc = next(item for item in documents if item["id"] == doc_id)
            if doc["status"] not in {"pending", "processing"} or time.monotonic() >= deadline:
                break
            time.sleep(1)
        record("knowledge upload / real embedding", doc["status"] == "done" and doc["chunk_count"] > 0,
               status=doc["status"], chunk_count=doc["chunk_count"], error=doc["error_msg"])
        if doc["status"] == "done":
            answer = check_stream("knowledge retrieval / real model / source", {
                "session_id": "api-check", "query": "验收代号是什么？请只回答代号。", "knowledge_ids": [kb_id],
            }, "vector")
            record("answer grounded in uploaded fixture", "API-CHECK-20261029" in answer)
        if settings.tavily_api_key:
            check_stream("configured web search / source", {
                "session_id": "api-check-web", "query": "Python official documentation website",
                "scope_vector": False, "scope_web": True,
            }, "web")
    except Exception as error:
        record("unexpected live check exception", False, error_type=type(error).__name__)
    finally:
        if doc_id and kb_id:
            try:
                request("DELETE", f"/api/v1/knowledge/{kb_id}/documents/{doc_id}")
                record("document delete / empty list", request("GET", f"/api/v1/knowledge/{kb_id}/documents") == [])
            except Exception as error:
                record("document cleanup", False, error_type=type(error).__name__)
        for resource_id, path in ((kb_id, "/api/v1/knowledge/"), (model_id, "/api/v1/admin/llm-configs/")):
            if resource_id:
                try:
                    request("DELETE", path + resource_id)
                    record("cleanup " + path, True)
                except Exception as error:
                    record("cleanup " + path, False, error_type=type(error).__name__)
        with open("/tmp/interface-live-results.json", "w", encoding="utf-8") as output:
            json.dump(results, output, ensure_ascii=False, indent=2)
    return 0 if results and all(item["passed"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
