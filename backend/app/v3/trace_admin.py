# 问答审计读模型：具备 traces.view 的人员可查看本企业成员的问答结果。
# 审计读取与个人会话入口分开；详情使用字段白名单，查看行为写入操作审计。

"""当前租户内的管理员问答审计视图。"""
import math

from sqlalchemy import case, func, select

from .domain import DomainError, one
from .models import Conversation, Message


# 仅接收有限数值，避免错误耗时或布尔值混入审计展示。
def _number(value):
    return value if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) else None


# 把非字符串元数据收敛为空文本，保持历史记录展示稳定。
def _text(value):
    return value if isinstance(value, str) else ''


# 只返回已知阶段与失败字段，不把存储中额外键直接透传给界面。
def _trace(raw):
    raw = raw if isinstance(raw, dict) else {}
    stages = []
    # 历史 JSON 可能格式异常，逐层检查类型并只选展示字段，不能直接返回整个存储对象。
    for step in raw.get('stages', []) if isinstance(raw.get('stages'), list) else []:
        if not isinstance(step, dict):
            continue
        stages.append({'stage': _text(step.get('stage')), 'milliseconds': _number(step.get('milliseconds')),
                       'outcome': _text(step.get('outcome'))})
    failure = raw.get('failure')
    return {'stages': stages, 'failure': {'code': _text(failure.get('code')),
            'detail': _text(failure.get('detail'))} if isinstance(failure, dict) else None}


# 构造列表摘要，整体耗时取 request 阶段，不加载额外模型请求信息。
def _item(message):
    trace = _trace(message.trace)
    return {'id': message.id, 'conversation_id': message.conversation_id, 'owner': message.owner,
            'query': message.query, 'state': message.state, 'created_at': message.created_at,
            'total_milliseconds': next((s['milliseconds'] for s in trace['stages'] if s['stage'] == 'request'), None),
            'failure': trace['failure'], 'has_feedback': bool(message.feedback)}


# 验证筛选时间是非负有限 Unix 秒数，防止无效范围进入查询。
def _time(value):
    # bool 在 Python 中属于 int，必须先排除；再拒绝 NaN、无穷和负数，避免筛选范围失真。
    if value is None:
        return None
    if isinstance(value, bool):
        raise DomainError('时间必须是 Unix 秒数', 400)
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise DomainError('时间必须是 Unix 秒数', 400)
    if not math.isfinite(result) or result < 0:
        raise DomainError('时间必须是有效的 Unix 秒数', 400)
    return result


# 在租户与权限范围内筛选、聚合并分页；问题关键词按字面匹配，避免把百分号当通配符。
def list_traces(domain, tenant, user, *, page=1, page_size=20, query='', owner='', state='', start=None, end=None):
    _pagination(page, page_size)
    filters = _message_filters(tenant, query, owner, state, start, end)
    with domain.db.transaction(tenant, shared=True) as s:
        domain.require_permission(s, tenant, user, 'traces.view')
        # 所有计数和分页复用同一组筛选条件；关键词转义 %/_，按用户输入的字面文本搜索。
        counts = dict(s.execute(select(Message.state, func.count()).where(*filters).group_by(Message.state)).all())
        total = sum(counts.values())
        feedback = s.scalar(select(func.count()).select_from(Message).where(*filters, Message.feedback != ''))
        messages = s.scalars(select(Message).where(*filters).order_by(Message.created_at.desc(), Message.id.desc())
                             .offset((page - 1) * page_size).limit(page_size)).all()
        # 时间相同时用 ID 再排序，让分页顺序稳定；汇总统计覆盖筛选全集而不是当前页。
        return {'items': [_item(m) for m in messages], 'total': total, 'page': page, 'page_size': page_size,
                'summary': {'total': total, 'completed': counts.get('completed', 0), 'failed': counts.get('failed', 0),
                            'processing': counts.get('processing', 0), 'with_feedback': feedback}}


# 按租户取得消息并白名单输出来源与处理明细，同时记录是谁查看了哪条问答。
def trace_detail(domain, tenant, user, message_id):
    with domain.db.transaction(tenant) as s:
        domain.require_permission(s, tenant, user, 'traces.view')
        message = one(s, Message, tenant, message_id)
        domain.audit(s, tenant, user, 'trace.view', message_id)
        return _detail(message, tenant)


def _detail(message, tenant):
    """单条与会话审计共用白名单，避免新入口暴露额外存储字段。"""
    evidence = []
    for entry in message.evidence or []:
        # 审计可跨成员读取，但仍不能跨租户；来源与定位仅输出白名单，排除内部连接字段。
        if not isinstance(entry, dict) or entry.get('tenant_id', tenant) != tenant:
            continue
        item = {key: entry[key] for key in ('document_id', 'version_id', 'chunk_id', 'content', 'score', 'locator_level')
                if key in entry and isinstance(entry[key], (str, int, float))}
        locator = entry.get('locator')
        item['locator'] = {key: locator[key] for key in ('type', 'page', 'page_number', 'block', 'sheet', 'row', 'row_start', 'row_end',
                                                      'column', 'paragraph', 'section', 'title', 'start', 'end', 'partial', 'position')
                           if key in locator and isinstance(locator[key], (str, int, float))} if isinstance(locator, dict) else {}
        evidence.append(item)
    return {**_item(message), 'answer': message.answer, 'feedback': message.feedback,
            'evidence': evidence, 'trace': _trace(message.trace)}


def _pagination(page, page_size):
    if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
        raise DomainError('页码必须大于零，每页数量应为 1 至 100', 400)


def _message_filters(tenant, query='', owner='', state='', start=None, end=None):
    # 两种审计列表共用边界校验和字面匹配，筛选差异不能让新入口绕过原接口的限制。
    start, end = _time(start), _time(end)
    if start is not None and end is not None and start > end:
        raise DomainError('开始时间不能晚于结束时间', 400)
    if state not in ('', 'processing', 'completed', 'failed'):
        raise DomainError('问答状态无效', 400)
    filters = [Message.tenant_id == tenant]
    if query.strip():
        filters.append(Message.query.contains(query.strip(), autoescape=True))
    if owner.strip():
        filters.append(Message.owner == owner.strip())
    if state:
        filters.append(Message.state == state)
    if start is not None:
        filters.append(Message.created_at >= start)
    if end is not None:
        filters.append(Message.created_at <= end)
    return filters


def _title(title, first_query):
    # 管理员或成员自定义的主题保持原样；只有默认标题才从首问生成，不能拿命中追问冒充主题。
    if title and title not in ('新会话', ''):
        return title
    question = ' '.join((first_query or '').split())
    return question[:32] + ('…' if len(question) > 32 else '') if question else '新会话'


def _state_counts():
    return [func.sum(case((Message.state == state, 1), else_=0)).label(state)
            for state in ('completed', 'failed', 'processing')] + [
                func.sum(case((Message.feedback != '', 1), else_=0)).label('with_feedback')]


def list_conversations(domain, tenant, user, *, page=1, page_size=20, query='', owner='', state='', start=None, end=None):
    """先选中有匹配消息的会话，再统计完整会话并分页，避免同一会话被拆到多个列表页。"""
    _pagination(page, page_size)
    filters = _message_filters(tenant, query, owner, state, start, end)
    with domain.db.transaction(tenant, shared=True) as s:
        domain.require_permission(s, tenant, user, 'traces.view')
        matched = (select(Message.conversation_id, Message.owner, func.count().label('matching_messages'))
                   .where(*filters).group_by(Message.conversation_id, Message.owner).subquery())
        # 除租户外同时核对所有者；历史异常记录不能借用另一个成员的会话 ID 混入上下文。
        grouped = (select(Conversation.id, Conversation.owner, Conversation.title,
                          func.min(Message.created_at).label('first_at'), func.max(Message.created_at).label('last_at'),
                          func.count().label('message_count'), *_state_counts(), matched.c.matching_messages)
                   .join(Message, (Message.tenant_id == Conversation.tenant_id) &
                         (Message.conversation_id == Conversation.id) & (Message.owner == Conversation.owner))
                   .join(matched, (matched.c.conversation_id == Conversation.id) & (matched.c.owner == Conversation.owner))
                   .where(Conversation.tenant_id == tenant)
                   .group_by(Conversation.id, Conversation.owner, Conversation.title, matched.c.matching_messages).subquery())
        # 汇总覆盖筛选命中的全部会话；匹配消息数独立于完整上下文消息数，界面明确区分两者。
        fields = {'messages': 'message_count', 'matching_messages': 'matching_messages', 'completed': 'completed',
                  'failed': 'failed', 'processing': 'processing', 'with_feedback': 'with_feedback'}
        summary = dict(s.execute(select(func.count().label('conversations'), *[
            func.coalesce(func.sum(grouped.c[column]), 0).label(key) for key, column in fields.items()])).mappings().one())
        first_query = (select(Message.query).where(Message.tenant_id == tenant, Message.conversation_id == grouped.c.id,
                                                  Message.owner == grouped.c.owner)
                       .order_by(Message.created_at, Message.id).limit(1).correlate(grouped).scalar_subquery())
        records = s.execute(select(grouped, first_query.label('first_query'))
                            .order_by(grouped.c.last_at.desc(), grouped.c.id.desc())
                            .offset((page - 1) * page_size).limit(page_size)).mappings().all()
        items = []
        for record in records:
            item = dict(record)
            item['title'] = _title(item['title'], item.pop('first_query'))
            items.append(item)
        return {'items': items, 'total': summary['conversations'], 'page': page, 'page_size': page_size, 'summary': summary}


def conversation_detail(domain, tenant, user, conversation_id, *, page=1, page_size=20):
    """审计会话的全部上下文，长会话按问答轮次分页，逐条记录本页实际查看行为。"""
    _pagination(page, page_size)
    with domain.db.transaction(tenant) as s:
        domain.require_permission(s, tenant, user, 'traces.view')
        conversation = one(s, Conversation, tenant, conversation_id)
        filters = [Message.tenant_id == tenant, Message.conversation_id == conversation.id, Message.owner == conversation.owner]
        stats = dict(s.execute(select(func.count().label('total'), func.min(Message.created_at).label('first_at'),
                                      func.max(Message.created_at).label('last_at'), *_state_counts()).where(*filters)).mappings().one())
        first_query = s.scalar(select(Message.query).where(*filters).order_by(Message.created_at, Message.id).limit(1))
        messages = s.scalars(select(Message).where(*filters).order_by(Message.created_at, Message.id)
                             .offset((page - 1) * page_size).limit(page_size)).all()
        # 不把列表的关键词/状态筛选带入详情，否则追问会失去它依赖的前文；返回字段仍复用白名单。
        items = []
        for message in messages:
            items.append(_detail(message, tenant))
            domain.audit(s, tenant, user, 'trace.view', message.id)
        return {'id': conversation.id, 'owner': conversation.owner, 'title': _title(conversation.title, first_query),
                **stats, 'page': page, 'page_size': page_size, 'items': items}
