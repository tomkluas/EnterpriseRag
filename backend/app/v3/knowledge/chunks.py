# 分块预览的只读响应契约：仅输出必要字段，不透传服务内部元数据或附件地址。
from .contracts import BackendError


def validate_page(page, page_size):
    if type(page) is not int or type(page_size) is not int or not 1 <= page <= 10000 or not 1 <= page_size <= 20:
        raise BackendError('invalid_query', '分块页码应为 1 至 10000，每页数量应为 1 至 20')


def chunk_page(data, binding, provider, page, page_size):
    rows = data.get('data'); total = data.get('total')
    # 不接受重复身份或超出请求大小的响应，不能把错误分页伪装成完整结果。
    if (not isinstance(rows, list) or type(total) is not int or total < 0 or len(rows) > page_size
            or len(rows) > max(0, total - (page - 1) * page_size)
            or data.get('page', page) != page or data.get('limit' if provider == 'dify' else 'page_size', page_size) != page_size):
        raise BackendError('invalid_response')
    items = []; seen = set()
    for row in rows:
        if not isinstance(row, dict):raise BackendError('invalid_response')
        if provider == 'weknora':
            if row.get('knowledge_id') != binding.remote_id or row.get('knowledge_base_id') != binding.collection_id:
                raise BackendError('invalid_scope')
            position = row.get('chunk_index')
            position = position + 1 if type(position) is int and position >= 0 else None
            sources = row.get('source_locators')
            raw = sources[0] if isinstance(sources, list) and sources and isinstance(sources[0], dict) else {}
            locator = {key: value for key, value in raw.items()
                       if key in ('type','page','block','sheet','row_start','row_end','start','end','partial')
                       and isinstance(value, (str,int,float,bool))}
        else:
            if row.get('document_id') != binding.remote_id:raise BackendError('invalid_scope')
            position = row.get('position'); position = position if type(position) is int and position > 0 else None
            locator = {}  # Dify 的 position 是分块顺序，不是原文件页码。
        id = row.get('id'); content = row.get('content')
        if not isinstance(id, str) or not id or id in seen or not isinstance(content, str):
            raise BackendError('invalid_response')
        seen.add(id)
        # 正文按纯文本预览，单块过长时明确标记截取，不能声称已经展示完整内容。
        enabled = row.get('enabled' if provider == 'dify' else 'is_enabled')
        kind = row.get('chunk_type','text') if provider=='weknora' else 'text'
        state = row.get('index_status','') if provider=='weknora' else row.get('status','')
        if not isinstance(kind,str) or not isinstance(state,str) or len(kind)>64 or len(state)>64:
            raise BackendError('invalid_response')
        items.append({'id':id, 'position':position, 'content':content[:20000], 'characters':len(content),
                      'truncated':len(content)>20000, 'type':kind, 'state':state,
                      'enabled':enabled if type(enabled) is bool else None, 'locator':locator})
    return {'items':items, 'total':total, 'page':page, 'page_size':page_size, 'has_more':page*page_size<total}
