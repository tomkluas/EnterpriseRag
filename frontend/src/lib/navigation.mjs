// 菜单按实际操作权限生成，普通阅读者只显示知识问答。
export function navigationFor(permissions=[]) {
  const has=permission=>permissions.includes(permission)
  const maintainer=permissions.some(p=>['knowledge.create','knowledge.manage','system.manage'].includes(p))
  return [
    {id:'chat',title:'知识问答',symbol:'⌕'},
    ...(maintainer?[{id:'documents',title:'知识资料',symbol:'▤'}]:[]),
    ...(has('system.manage')?[{id:'backends',title:'知识底座',symbol:'⇄'}]:[]),
    ...(maintainer?[{id:'jobs',title:'处理任务',symbol:'◷'}]:[]),
    ...(permissions.some(p=>['system.manage','organization.manage','members.manage','roles.manage'].includes(p))?[{id:'directory',title:'企业与权限',symbol:'♙'}]:[]),
    ...(has('traces.view')?[{id:'traces',title:'问答审计',symbol:'◎'}]:[]),
    ...(has('system.manage')?[{id:'audit',title:'操作记录',symbol:'≡'}]:[])
  ]
}
