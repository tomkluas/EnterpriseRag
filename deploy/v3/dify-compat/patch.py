"""Narrow compatibility patch for pinned Dify 1.18.0; fails on source drift.

The API already validated the tenant and dataset scope. Populate the dataset
creator's runtime tenant using Dify's own membership lookup, without granting
membership or skipping any authorization check.
"""
from pathlib import Path
import hashlib

# 只匹配固定版本的创建者读取函数；下方字符串为待替换源码。
target=Path('/app/api/repositories/knowledge/dataset_read_repository.py')
old='''def get_dataset_creator(dataset: Dataset, *, session: Session) -> Account | None:
    return session.get(Account, dataset.created_by)
'''
new='''def get_dataset_creator(dataset: Dataset, *, session: Session) -> Account | None:
    account = session.get(Account, dataset.created_by)
    if account is not None:
        account.set_tenant_id_with_session(dataset.tenant_id, session=session)
    return account
'''
# 补齐已查出创建者的运行时租户上下文，不新增成员关系或绕过授权。
source=target.read_text()
# 源码偏移或重复应用立即失败，避免悄悄修改未知版本。
if source.count(old)!=1:
    raise SystemExit('Pinned Dify source differs; stop and review compatibility patch')
# 记录修改前摘要，便于从构建日志追溯实际补丁目标。
print('Original repository module sha256:',hashlib.sha256(source.encode()).hexdigest())
target.write_text(source.replace(old,new))
