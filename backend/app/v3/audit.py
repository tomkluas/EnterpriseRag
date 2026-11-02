"""操作记录的分页查询；名称补充与筛选均在当前租户内完成。"""
from sqlalchemy import select, func, case, or_, literal
from .models import Audit, Document, Version, Space, BackendInstance


ACTION_NAMES = {
    'space.create':'新建知识库','space.grant':'修改知识库授权','document.receive':'上传文件或新版本',
    'document.publish':'发布资料','document.withdraw':'撤回资料','document.grant':'修改资料授权',
    'document.download':'下载原件','version.cancel':'取消失败草稿','backend.register':'登记知识服务',
    'backend.accept_validation':'导入底座验证结果','backend.prepare':'准备底座切换','backend.activate':'切换知识底座',
    'job.retry':'重试处理任务','job.reconcile':'核对上传结果','original.orphan_delete':'回收孤立原件',
    'conversation.create':'新建会话','conversation.delete':'删除个人会话','message.ask':'提交问题',
    'message.feedback':'提交或修改回答反馈','trace.view':'查看问答审计详情','user.update':'新增或修改成员',
    'user.login':'账号登录','user.logout':'退出登录','role.save':'新增或修改角色','role.delete':'删除角色',
    'organization.save':'新增或修改部门','organization.delete':'删除部门','tenant.update':'修改企业信息'
}


def list_audit(domain, tenant, user, *, page=1, page_size=20, query='', category=''):
    from .domain import DomainError, dump
    if type(page) is not int or type(page_size) is not int or page < 1 or not 1 <= page_size <= 100:
        raise DomainError('页码必须大于零，每页数量应为 1 至 100',400)
    with domain.db.transaction(tenant,shared=True) as s:
        domain.admin(s,tenant,user)

        def name(model, field):
            return select(getattr(model,field)).where(model.tenant_id==tenant,
                model.id==Audit.resource_id).correlate(Audit).scalar_subquery()

        resource_name=func.coalesce(case(
            (Audit.action.startswith('version.'),name(Version,'filename')),
            (Audit.action.startswith('document.'),name(Document,'name')),
            (or_(Audit.action.startswith('space.'),Audit.action.in_(['backend.activate','backend.prepare'])),name(Space,'name')),
            (Audit.action.startswith('backend.'),name(BackendInstance,'provider')),
            (Audit.action.startswith('conversation.'),literal('个人会话')),
            (or_(Audit.action.startswith('message.'),Audit.action=='trace.view'),literal('问答消息')),
            (Audit.action.startswith('job.'),literal('处理任务')),
            else_=literal('')),literal(''))
        statement=select(Audit,resource_name.label('resource_name')).where(Audit.tenant_id==tenant)
        category=category.strip()
        if category:
            statement=statement.where(Audit.action.startswith(category+'.',autoescape=True))
        query=query.strip().lower()
        if query:
            # 将 % 和 _ 当普通字符处理；中文操作名称与关联资源名称也参与全量筛选。
            matching_actions=[action for action,label in ACTION_NAMES.items() if query in label.lower()]
            statement=statement.where(or_(
                *(func.lower(value).contains(query,autoescape=True) for value in
                  (Audit.actor,Audit.action,Audit.resource_id,resource_name)),
                Audit.action.in_(matching_actions)))
        total=s.scalar(select(func.count()).select_from(statement.subquery()))
        records=s.execute(statement.order_by(Audit.created_at.desc(),Audit.id.desc())
            .offset((page-1)*page_size).limit(page_size))
        items=[{**dump(record),'resource_name':label,
            'action_name':ACTION_NAMES.get(record.action,record.action)} for record,label in records]
        return {'items':items,'total':total,'page':page,'page_size':page_size}
