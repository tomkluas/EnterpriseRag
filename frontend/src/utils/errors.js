export function errorMessage(error, fallback = '请求失败') {
  const detail = error?.response?.data?.detail ?? error?.detail ?? error
  if (typeof detail === 'string') return detail || fallback
  if (Array.isArray(detail)) return detail.map(item => errorMessage(item, fallback)).join('；')
  return detail?.message || detail?.msg || fallback
}
