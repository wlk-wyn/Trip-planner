type Forecast = {
  day: string; night?: string; high?: unknown; low?: unknown
  source?: string | null; observed_at?: string | null; report_time?: string | null
}
function degrees(value: unknown): number | null {
  if (typeof value !== 'number' && typeof value !== 'string') return null
  if (typeof value === 'string' && !value.trim()) return null
  const number=Number(value)
  return Number.isFinite(number) && number>=-100 && number<=100 ? number : null
}

const reportInstant=(value:string)=>Date.parse(value.replace(' ','T')+(/Z$|[+-]\d{2}:\d{2}$/.test(value)?'':'+08:00'))

/** Saved or fresh API weather must carry actual query/report evidence to be displayed. */
export function forecastDisplay(forecast: Forecast | null | undefined, now=Date.now()) {
  if (!forecast || forecast.source!=='amap' || !forecast.observed_at || !forecast.report_time || !forecast.day) return null
  const query=Date.parse(forecast.observed_at)
  const report=reportInstant(forecast.report_time)
  if (![query,report].every(time=>Number.isFinite(time) && time<=now+300000 && now-time<=86400000)) return null
  const high=degrees(forecast.high),low=degrees(forecast.low)
  const temperature=high!=null && low!=null ? `${low}–${high}°C` : high!=null ? `最高 ${high}°C` : low!=null ? `最低 ${low}°C` : ''
  return {text:`${forecast.day}${temperature?` ${temperature}`:''} · 短期预报`,wet:/雨|雪/.test(forecast.day+(forecast.night||''))}
}

export function forecastEvidence(forecast: Forecast | null | undefined, now=Date.now()) {
  if (!forecast || !forecastDisplay(forecast,now)) return null
  const format=new Intl.DateTimeFormat('zh-CN',{timeZone:'Asia/Shanghai',year:'numeric',month:'2-digit',day:'2-digit',hour:'2-digit',minute:'2-digit',hour12:false})
  return {reportTime:format.format(reportInstant(forecast.report_time!)),queryTime:format.format(Date.parse(forecast.observed_at!))}
}
