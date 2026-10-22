import type { FactEvidence, FactState } from '@/types'

export function factDescription(fact?: FactState): string {
  if (!fact || fact.status === 'missing') return '暂无信息'
  if (fact.status === 'scope_mismatch') return `价格原按 ${fact.applies_on || '其他日期'} 参考，游览日期已变化，请更新`
  if (fact.status === 'unknown_age' || !fact.recorded_at) return '记录时间不明，需核对'
  const date = new Date(fact.recorded_at)
  const stamp = Number.isNaN(date.getTime()) ? '时间不明' : date.toLocaleDateString('zh-CN')
  if (fact.status === 'model_estimate') return `模型估算 · ${stamp} 生成，未核实`
  if (fact.status === 'user_provided') return `用户提供 · ${stamp} 记录，未独立核实`
  if (fact.source === 'official') return `官网公示参考 · ${stamp} 查询${fact.status === 'stale_reference' ? '，建议更新' : ''}${fact.applies_on ? ` · 按 ${fact.applies_on} 作规划参考` : ''}${fact.scope ? `；${fact.scope}` : ''}`
  return `高德参考 · ${stamp} 查询${fact.status === 'stale_reference' ? '，建议更新' : ''}`
}

const officialUrls = new Set([
  'https://www.dpm.org.cn/singles_detail/257830.html',
  'https://www.bmy.com.cn/jingtai/bmyweb/ticketing.html',
])
export function officialSourceUrl(fact?: FactState): string | null {
  return fact?.source === 'official' && fact.source_url && officialUrls.has(fact.source_url) ? fact.source_url : null
}

export function officialPriceNeedsUpdate(evidence: FactEvidence | null | undefined, visitDate?: string): boolean {
  return evidence?.source === 'official' && (!visitDate || evidence.applies_on !== visitDate)
}
