```bash
# Phase 1 验收
cp .env.example .env
# 编辑.env填写SECRET_KEY和ENCRYPTION_KEY（32字节hex）

cd backend && pip install -r requirements.txt
python scripts/init_db.py
uvicorn app.main:app --port 8000

curl http://localhost:8000/api/health
# 期望: {"status": "ok"}

curl -X POST http://localhost:8000/api/v1/auth/login \
  -H "Content-Type: application/json" \
  -d '{"username":"admin","password":"Admin@ChangeMe123"}'
```
