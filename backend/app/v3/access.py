# 当前公司内的组织与 RBAC 管理：成员属于部门，成员关联角色，资料规则只引用角色。
# 部门上下级不隐式授权；内置角色提供默认系统能力，业务角色可独立配置。

"""当前租户内的组织结构与角色权限管理。"""
import hashlib
from sqlalchemy import select, delete
from .models import Tenant, User, Role, UserRole, OrganizationUnit, Space, Document

PERMISSIONS = {
    'knowledge.read': ('阅读知识资料','阅读获得角色授权的资料并提问'),
    'knowledge.create': ('创建和维护自己的知识库','创建知识库，管理本人负责的资料与授权'),
    'knowledge.manage': ('管理全部知识库','管理当前公司全部知识库、版本与资料授权'),
    'members.manage': ('管理成员','创建、停用成员并分配组织和角色'),
    'roles.manage': ('管理角色','维护角色及其系统操作权限'),
    'organization.manage': ('管理组织','维护公司的组织架构'),
    'traces.view': ('问答审计','查看本公司所有成员的问答结果、反馈和处理明细'),
    'system.manage': ('系统管理','维护企业信息、知识服务、任务与操作记录'),
}
BUILTINS = {
    'admin': ('系统管理员',list(PERMISSIONS)),
    'editor': ('资料管理员',['knowledge.read','knowledge.create']),
    'reader': ('知识使用者',['knowledge.read']),
}


# 以租户和用途生成稳定标识，确保内置角色及迁移角色在重复执行时不会重复创建。
def stable_id(tenant,subject):
    return hashlib.sha256((tenant+'\0'+subject).encode()).hexdigest()[:32]


# 补齐公司根部门与三个内置角色；只创建缺失记录，不覆盖已有角色配置。
def seed_access(s,tenant):
    # 稳定标识配合存在性检查让初始化可重复执行，已有角色的管理员配置不被默认值覆盖。
    tr=s.get(Tenant,tenant)
    root=stable_id(tenant,'root')
    if not s.get(OrganizationUnit,(tenant,root)):
        s.add(OrganizationUnit(tenant_id=tenant,id=root,name=tr.name or '企业'))
    for code,(name,permissions) in BUILTINS.items():
        id=stable_id(tenant,'builtin:'+code)
        if not s.get(Role,(tenant,id)):
            s.add(Role(tenant_id=tenant,id=id,code=code,name=name,permissions=permissions,
                builtin=True,description='内置系统角色'))
    s.flush()
    return root


# 把旧身份规则转为专用角色：负责人仅匹配本人，旧规则保留原有用户/组匹配语义。
def legacy_role(s,tenant,subject,*,owner=False):
    # 区分负责人身份和旧规则身份：同名 group: 前缀作为负责人时仍只代表本人。
    tagged=('user:' if owner else 'principal:')+subject
    id=stable_id(tenant,'legacy:'+tagged)
    role=s.get(Role,(tenant,id))
    if role is None:
        label=('历史资料负责人 · '+subject) if owner else ('历史组授权 · '+subject[6:] if subject.startswith('group:') else '历史个人授权 · '+subject)
        role=Role(tenant_id=tenant,id=id,name=label[:128],
            description='保留旧授权的原访问范围，可整理为业务角色后移除',permissions=['knowledge.read'],legacy_subject=tagged)
        s.add(role);s.flush()
    for member in s.scalars(select(User).filter_by(tenant_id=tenant)):
        matched=member.id==subject or (not owner and subject.startswith('group:') and subject[6:] in member.groups)
        # 只补齐实际命中的成员关系，不能把旧个人授权替换成所有 reader 都有的内置角色。
        if matched and not s.get(UserRole,(tenant,member.id,id)):
            s.add(UserRole(tenant_id=tenant,user_id=member.id,role_id=id))
    s.flush()
    return 'role:'+id


# 仅对旧授权版本迁移：保留原访问范围并冻结旧资料的有效规则，避免默认角色扩权。
def migrate_tenant_access(s,tenant):
    tr=s.get(Tenant,tenant)
    if tr.access_version>=1:return
    # 先迁移成员身份，再改写资源规则；版本标记最后更新，失败随外层事务回滚。
    root=seed_access(s,tenant)
    for member in s.scalars(select(User).filter_by(tenant_id=tenant)):
        id=stable_id(tenant,'builtin:'+member.role)
        if member.role not in BUILTINS: id=stable_id(tenant,'builtin:reader')
        if not s.get(UserRole,(tenant,member.id,id)):
            s.add(UserRole(tenant_id=tenant,user_id=member.id,role_id=id))
        if not member.org_unit_id:
            member.org_unit_id=root
            for group in member.groups:
                department=stable_id(tenant,'legacy-org:'+group)
                if not s.get(OrganizationUnit,(tenant,department)):
                    s.add(OrganizationUnit(tenant_id=tenant,id=department,name=group[:128],parent_id=root));s.flush()
            if member.groups:member.org_unit_id=stable_id(tenant,'legacy-org:'+member.groups[0])
    spaces=list(s.scalars(select(Space).filter_by(tenant_id=tenant)))
    original_readers={space.id:list(space.readers) for space in spaces}
    # 保存改写前的知识库允许列表，旧文档空规则需要按原始默认范围冻结。
    for space in spaces:
        old=list(space.readers)
        space.readers=list(dict.fromkeys([legacy_role(s,tenant,value) for value in old]+[legacy_role(s,tenant,space.owner,owner=True)]))
        space.denied=[legacy_role(s,tenant,value) for value in space.denied]
    for doc in s.scalars(select(Document).filter_by(tenant_id=tenant)):
        space=next((row for row in spaces if row.id==doc.space_id),None)
        # 冻结迁移前的有效授权范围，负责人原有权限只保留给本人，不扩展给同角色的其他成员。
        effective=doc.readers or (original_readers.get(space.id,[]) if space else [])
        # 每份旧文档显式保存原范围并关闭继承，之后修改默认规则不会悄然扩权。
        doc.readers=list(dict.fromkeys([legacy_role(s,tenant,value) for value in effective]+[legacy_role(s,tenant,doc.owner,owner=True)]))
        doc.denied=[legacy_role(s,tenant,value) for value in doc.denied]
        doc.inherit_permissions=False
    tr.access_version=1;tr.authz+=1;s.flush()


# 提供 Domain 复用的角色、组织与资料授权能力；所有入口都必须保持租户范围。
class AccessManagement:
    # 读取当前仍启用的成员角色；停用角色不参与系统权限和资料权限判断。
    def effective_roles(self,s,tenant,user):
        # 连接条件同时匹配租户和角色标识，停用角色直接从有效集合剔除。
        return list(s.scalars(select(Role).join(UserRole,(UserRole.tenant_id==Role.tenant_id)&(UserRole.role_id==Role.id))
            .where(Role.tenant_id==tenant,UserRole.user_id==user,Role.active.is_(True))))

    # 合并有效角色的操作能力；判断依据是权限标识，不是界面显示的角色名称。
    def permissions(self,s,tenant,user):
        return {permission for role in self.effective_roles(s,tenant,user) for permission in role.permissions}

    # 同时检查成员/企业有效性与所需操作权限，供管理接口在事务内再次校验。
    def require_permission(self,s,tenant,user,permission):
        from .domain import DomainError
        self.user(s,tenant,user)
        if permission not in self.permissions(s,tenant,user):raise DomainError('没有执行此操作的权限',403)

    # 资料与任务工作区只向维护人员开放；阅读权限用于问答及其引用，不授予工作区访问权。
    def require_knowledge_workspace(self,s,tenant,user):
        from .domain import DomainError
        self.user(s,tenant,user)
        if not self.permissions(s,tenant,user).intersection({'knowledge.create','knowledge.manage','system.manage'}):
            raise DomainError('没有查看知识资料和处理任务的权限',403)

    # 在已建立的业务上下文中查询某项操作能力，用于资源管理与阅读权的分离。
    def has_permission(self,s,tenant,user,permission):
        return permission in self.permissions(s,tenant,user)

    # 组装对外身份信息；保留已分配的停用角色供编辑，但只合并启用角色的有效权限。
    def identity(self,s,tenant,user,include_inactive=False):
        from .domain import dump,one
        member=one(s,User,tenant,user) if include_inactive else self.user(s,tenant,user)
        roles=list(s.scalars(select(Role).join(UserRole,(UserRole.tenant_id==Role.tenant_id)&(UserRole.role_id==Role.id))
            .where(Role.tenant_id==tenant,UserRole.user_id==user)))
        value=dump(member);value.pop('groups',None)
        # 分配列表保留停用角色用于编辑；实际权限只合并启用成员的启用角色。
        value.update(role_ids=[r.id for r in roles],role_names=[r.name for r in roles],
            permissions=sorted({p for r in roles if r.active and member.active for p in r.permissions}),tenant_name=s.get(Tenant,tenant).name)
        return value

    # 为资料维护者提供可选角色目录；只返回角色说明，不混入成员和部门授权选项。
    def role_options(self,s,tenant):
        return [{'id':r.id,'label':r.name,'kind':'role','active':r.active,'description':r.description}
            for r in s.scalars(select(Role).filter_by(tenant_id=tenant).order_by(Role.builtin.desc(),Role.name))]

    # 逐一验证角色属于当前租户；是否允许保留停用角色由调用场景决定。
    def validate_roles(self,s,tenant,ids,allow_inactive=False):
        from .domain import one,DomainError
        result=[]
        for id in dict.fromkeys(ids):
            row=one(s,Role,tenant,id)
            if not row.active and not allow_inactive:raise DomainError('不能分配已停用的角色',409)
            result.append(row)
        return result

    # 成员管理需要角色目录来分配角色，角色管理需要目录来维护定义。
    def list_roles(self,tenant,user):
        from .domain import dump,DomainError
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user)
            if not self.permissions(s,tenant,user).intersection({'roles.manage','members.manage'}):raise DomainError('没有查看角色目录的权限',403)
            return [dump(role) for role in s.scalars(select(Role).filter_by(tenant_id=tenant).order_by(Role.builtin.desc(),Role.name))]

    # 维护角色定义并递增权限修订号；内置角色的权限集合和启用状态固定。
    def save_role(self,tenant,user,name,description='',permissions=None,active=True,id=None):
        from .domain import one,uid,dump,DomainError
        permissions=list(dict.fromkeys(permissions or []))
        if not name.strip() or len(name)>128 or set(permissions)-set(PERMISSIONS):raise DomainError('角色名称或权限无效',400)
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,user,'roles.manage')
            if s.scalar(select(Role).where(Role.tenant_id==tenant,Role.name==name.strip(),Role.id!=(id or ''))):raise DomainError('角色名称已存在')
            role=one(s,Role,tenant,id) if id else Role(tenant_id=tenant,id=uid())
            # 内置角色允许改说明，但不能改变其权限或启用状态，保证基础管理能力稳定。
            if id and role.builtin and (set(role.permissions)!=set(permissions) or role.active!=active):raise DomainError('内置角色的权限和启用状态不可修改')
            if not id:s.add(role)
            role.name=name.strip();role.description=description;role.permissions=permissions;role.active=active
            s.get(Tenant,tenant).authz+=1;self.audit(s,tenant,user,'role.save',role.id);s.flush()
            return dump(role)

    # 删除前检查所有直接引用；将具体占用返回给管理员，不自动级联移除成员或资料授权。
    def delete_role(self,tenant,user,id):
        from .domain import one,DomainError
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,user,'roles.manage');role=one(s,Role,tenant,id)
            if role.builtin:raise DomainError('内置角色不能删除')
            references=self.role_references(s,tenant,id)
            # 先返回所有引用位置供人工调整，不级联移除授权，尤其不能删除禁止规则而放开访问。
            if references:
                raise DomainError(f'角色仍有 {len(references)} 处关联，请先调整下方成员和授权规则后重试',details={
                    'kind':'role_in_use','references':references})
            s.delete(role);s.get(Tenant,tenant).authz+=1;self.audit(s,tenant,user,'role.delete',id)

    # 汇总成员、知识库默认规则和资料规则中的直接引用，包含停用成员与撤回资料。
    def role_references(self,s,tenant,id):
        references=[];token='role:'+id
        # 停用成员和撤回资料也保留引用，否则未来恢复这些对象时会出现悬空规则。
        for membership in s.scalars(select(UserRole).filter_by(tenant_id=tenant,role_id=id).order_by(UserRole.user_id)):
            member=s.get(User,(tenant,membership.user_id))
            references.append({'type':'member','id':membership.user_id,'name':(member.display_name or member.id) if member else membership.user_id,
                'active':member.active if member else False,'location':'企业与权限 → 成员 → 编辑 → 角色分配','rules':[]})
        spaces={row.id:row for row in s.scalars(select(Space).filter_by(tenant_id=tenant).order_by(Space.name,Space.id))}
        for space in spaces.values():
            rules=[key for key,values in (('allow',space.readers),('deny',space.denied)) if token in values]
            if rules:references.append({'type':'space','id':space.id,'name':space.name,'location':f'知识资料 → {space.name} → 默认角色授权','rules':rules})
        for doc in s.scalars(select(Document).filter_by(tenant_id=tenant).order_by(Document.name,Document.id)):
            # 继承来自知识库的规则只在知识库列出，文档行仅报告它自己保存的直接引用。
            rules=[key for key,values in (('allow',doc.readers),('deny',doc.denied)) if token in values]
            if rules:
                space=spaces.get(doc.space_id)
                references.append({'type':'document','id':doc.id,'name':doc.name,'space_id':doc.space_id,'space_name':space.name if space else doc.space_id,
                    'withdrawn':doc.withdrawn,'location':f'知识资料 → {space.name if space else doc.space_id} → {doc.name} → 角色授权','rules':rules})
        return references

    # 按公司读取部门树的扁平记录，前端再组织层级；此目录本身不赋予资料访问权。
    def list_organization(self,tenant,user):
        from .domain import dump,DomainError
        with self.db.transaction(tenant,shared=True) as s:
            self.user(s,tenant,user)
            if not self.permissions(s,tenant,user).intersection({'organization.manage','members.manage'}):raise DomainError('没有查看组织架构的权限',403)
            return [dump(row) for row in s.scalars(select(OrganizationUnit).filter_by(tenant_id=tenant).order_by(OrganizationUnit.parent_id,OrganizationUnit.name))]

    # 校验父链、同级重名与启用关系；根部门名称通过企业信息统一修改。
    def save_organization(self,tenant,user,name,parent_id='',active=True,id=None):
        from .domain import one,uid,dump,DomainError
        if not name.strip() or len(name)>128:raise DomainError('部门名称无效',400)
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,user,'organization.manage')
            node=one(s,OrganizationUnit,tenant,id) if id else OrganizationUnit(tenant_id=tenant,id=uid())
            if id==stable_id(tenant,'root') and name.strip()!=node.name:raise DomainError('公司根部门名称请在企业信息中修改')
            if id==stable_id(tenant,'root') and parent_id:raise DomainError('公司根节点不能移动')
            if not parent_id and id!=stable_id(tenant,'root'):raise DomainError('请选择上级部门')
            visited={node.id};current=parent_id
            # 沿新父节点向上追溯，同时检查成环与启用关系；不能只验证直属父节点。
            while current:
                if current in visited:raise DomainError('上级部门不能是自己或下级部门')
                visited.add(current);parent=one(s,OrganizationUnit,tenant,current)
                if not parent.active and active:raise DomainError('启用部门的上级部门必须启用')
                current=parent.parent_id
            if s.scalar(select(OrganizationUnit).where(OrganizationUnit.tenant_id==tenant,OrganizationUnit.parent_id==parent_id,OrganizationUnit.name==name.strip(),OrganizationUnit.id!=node.id)):raise DomainError('同级部门名称已存在')
            if not active and s.scalar(select(OrganizationUnit).filter_by(tenant_id=tenant,parent_id=node.id,active=True).limit(1)):raise DomainError('请先停用下级部门')
            # 检查通过后才落字段，部门变更仅影响组织归属，不隐式改写资料授权。
            if node.id==stable_id(tenant,'root') and not active:raise DomainError('公司根部门不能停用')
            if not id:s.add(node)
            node.name=name.strip();node.parent_id=parent_id;node.active=active
            self.audit(s,tenant,user,'organization.save',node.id);s.flush();return dump(node)

    # 仅删除无下级、无所属成员的非根部门，避免产生悬空的组织关系。
    def delete_organization(self,tenant,user,id):
        from .domain import one,DomainError
        with self.db.transaction(tenant) as s:
            self.require_permission(s,tenant,user,'organization.manage');node=one(s,OrganizationUnit,tenant,id)
            if not node.parent_id:raise DomainError('公司根部门不能删除')
            if s.scalar(select(OrganizationUnit).filter_by(tenant_id=tenant,parent_id=id).limit(1)) or s.scalar(select(User).filter_by(tenant_id=tenant,org_unit_id=id).limit(1)):raise DomainError('请先移除下级部门或调整所属成员')
            s.delete(node);self.audit(s,tenant,user,'organization.delete',id)

    # 把已校验的角色写入知识库/资料规则，并递增权限修订号使进行中的读取重新校验。
    def set_resource_roles(self,tenant,user,id,role_ids,denied_role_ids,inherit=False,space=False):
        from .domain import one
        with self.db.transaction(tenant) as s:
            row=one(s,Space if space else Document,tenant,id)
            self.manage_space(s,tenant,user,row if space else one(s,Space,tenant,row.space_id))
            self.validate_roles(s,tenant,role_ids,allow_inactive=True);self.validate_roles(s,tenant,denied_role_ids,allow_inactive=True)
            # 允许保留停用角色的既有规则；规则只有在角色启用时才参与权限计算。
            row.readers=['role:'+id for id in dict.fromkeys(role_ids)];row.denied=['role:'+id for id in dict.fromkeys(denied_role_ids)]
            if not space:row.inherit_permissions=inherit
            # 同事务递增权限修订号，让进行中的问答在下一次外发或输出前发现撤权。
            s.get(Tenant,tenant).authz+=1;self.audit(s,tenant,user,'space.grant' if space else 'document.grant',id)
