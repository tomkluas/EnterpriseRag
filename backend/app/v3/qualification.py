# 解析验收证据筛选：只有格式、部署上下文和事实保真检查同时匹配的记录才能复用。
# context 各字段均精确相等，不能把别的端点、集合、镜像或模型的成功记录充作当前验收。
def matching_format_checks(rows,**context):
    """仅在服务地址、集合、镜像及模型完全相同时复用解析验收证据。"""
    return [row for row in rows if row.get('extension') in {'.pdf','.docx','.xlsx'}
        and row.get('status')=='passed' and row.get('fact_preserved') is True
        and all(row.get(key)==value for key,value in context.items())]
