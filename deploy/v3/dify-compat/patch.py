"""Narrow compatibility patch for pinned Dify 1.18.0; fails on source drift.

The API already validated the tenant and dataset scope. Populate the dataset
creator's runtime tenant using Dify's own membership lookup, without granting
membership or skipping any authorization check.
"""
from pathlib import Path
import hashlib

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
source=target.read_text()
if source.count(old)!=1:
    raise SystemExit('Pinned Dify source differs; stop and review compatibility patch')
print('Original repository module sha256:',hashlib.sha256(source.encode()).hexdigest())
target.write_text(source.replace(old,new))
