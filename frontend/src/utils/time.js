const CN_TZ = 'Asia/Shanghai'

export function formatDateTimeCN(value) {
  if (!value) return ''
  const raw = String(value)
  const normalized = raw.includes('T') ? raw : raw.replace(' ', 'T') + (raw.endsWith('Z') ? '' : 'Z')
  const d = new Date(normalized)
  if (Number.isNaN(d.getTime())) return raw.slice(0, 16)
  return new Intl.DateTimeFormat('zh-CN', {
    timeZone: CN_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
    hour12: false,
  }).format(d).replace(/\//g, '-')
}

export function todayCN() {
  const parts = new Intl.DateTimeFormat('zh-CN', {
    timeZone: CN_TZ,
    year: 'numeric',
    month: '2-digit',
    day: '2-digit',
  }).formatToParts(new Date())
  const map = Object.fromEntries(parts.map(p => [p.type, p.value]))
  return `${map.year}${map.month}${map.day}`
}

export function offsetDateCN(days = 0, months = 0) {
  const ymd = todayCN()
  const d = new Date(`${ymd.slice(0,4)}-${ymd.slice(4,6)}-${ymd.slice(6,8)}T12:00:00+08:00`)
  if (months) d.setMonth(d.getMonth() + months)
  if (days) d.setDate(d.getDate() + days)
  const y = d.getFullYear()
  const m = String(d.getMonth() + 1).padStart(2, '0')
  const day = String(d.getDate()).padStart(2, '0')
  return `${y}${m}${day}`
}
