// Provider output is text, never HTML. Only explicit platform hosts become links.
export type ProviderToken = { kind: 'text' | 'strong' | 'link' | 'image'; text: string; url?: string }
const linkHosts = ['meituan.com', 'dianping.com', 'dpurl.cn']
const imageHosts = [...linkHosts, 'meituan.net', 'dpfile.com']
export function providerUrl(value: string, image = false): string | null {
  try {
    if (/[\u0000-\u0020\u007f\\]/.test(value)) return null
    const url = new URL(value)
    if (url.username || url.password || url.port) return null
    if (url.protocol !== 'https:' && !(url.protocol === 'http:' && !image && url.hostname === 'dpurl.cn')) return null
    const hosts = image ? imageHosts : linkHosts
    return hosts.some(host => url.hostname === host || url.hostname.endsWith(`.${host}`)) ? url.href : null
  } catch { return null }
}
export function providerTokens(content: string): ProviderToken[] {
  const tokens: ProviderToken[] = []
  const pattern = /(!?)\[([^\]\n]*)\]\((https?:\/\/[^\s)]+)\)|\*\*([^*\n]+)\*\*|https?:\/\/[^\s<>\u3000，。；！？）】]+/g
  let cursor = 0
  for (const match of content.matchAll(pattern)) {
    if (match.index! > cursor) tokens.push({ kind: 'text', text: content.slice(cursor, match.index) })
    if (match[4]) tokens.push({ kind: 'strong', text: match[4] })
    else {
      const image = match[1] === '!'
      const value = match[3] || match[0]
      const url = providerUrl(value, image)
      tokens.push(url ? { kind: image ? 'image' : 'link', text: match[2] || value, url } : { kind: 'text', text: match[0] })
    }
    cursor = match.index! + match[0].length
  }
  if (cursor < content.length) tokens.push({ kind: 'text', text: content.slice(cursor) })
  return tokens
}

export function officialVisitUrl(value: string | undefined): string | null {
  if (!value || /[\u0000-\u0020\u007f\\]/.test(value)) return null
  try {
    const url = new URL(value)
    const hosts = ['www.dpm.org.cn', 'ticket.dpm.org.cn', 'm.chnmuseum.cn', 'pcticket.chnmuseum.cn', 'www.tiantanpark.cn', 'www.summerpalace.net.cn']
    return url.protocol === 'https:' && !url.username && !url.password && !url.port && hosts.includes(url.hostname) ? url.href : null
  } catch { return null }
}

export interface TicketOffer {
  title: string
  url: string
  titlePrice: string | null
  priceNote: string | null
  excludesAdmission: boolean
  details: string[]
}

/** Returned product links only; no inferred prices, quantities or arithmetic. */
export function ticketOffers(content: string): TicketOffer[] {
  const lines = content.split('\n')
  const offers: TicketOffer[] = []
  lines.forEach((line, index) => {
    const match = line.match(/^\s*(?:[-*]|\d+[.)、])?\s*\[([^\]\n]+)\]\((https?:\/\/[^\s)]+)\)\s*$/)
    if (!match || !/门票|联票|讲解|精讲|套餐|入园|参观票/.test(match[1])) return
    const url = providerUrl(match[2])
    if (!url) return
    const label = match[1].trim()
    const price = label.match(/([¥￥]\s*[\dXx][\dXx,.]*(?:\s*起)?(?:\s*[/／]\s*(?:人|位|张|份|晚|间|套|单))?)$/)
    const title = price ? label.slice(0, price.index).trim() : label
    if (!title) return
    const nextLine = lines.slice(index + 1).find(value => value.trim())?.trim() || ''
    const priceNote = /^(?:合计|总计|价格|套餐价|售价|门票价)/.test(nextLine.replace(/\*\*/g, '')) ? nextLine : null
    const details: string[] = []
    for (const following of lines.slice(index + 1, index + 16)) {
      const plain = following.replace(/\*\*/g, '').replace(/^\s*[-*]\s*/, '').trim()
      if (/^(?:包含|不含|费用包含|费用不含|适用人群|适用日期|数量条件|场次)[：:]?/.test(plain)) {
        details.push(following.trim())
        if (details.length === 3) break
      } else if (/^\s*(?:#{1,6}\s|[-*]?\s*\[|\*\*[^*]+\*\*\s*[：:])/.test(following)) break
    }
    offers.push({ title, url, titlePrice: price?.[1] || null, priceNote,
      excludesAdmission: /不含(?:景区)?(?:大)?门票/.test(label), details })
  })
  return offers
}

export interface TicketPriceReference { title: string; price: string; condition: string }

/** Explicit price lines only. These remain provider references, never live offers. */
export function ticketPriceReferences(content: string): TicketPriceReference[] {
  const references: TicketPriceReference[] = []
  for (const line of content.split('\n')) {
    // Linked products have their own cards. Skip prose without a named price row.
    if (/\]\(https?:\/\//.test(line)) continue
    const text = line.replace(/\*\*/g, '').replace(/^\s*(?:[-*]|\d+[.)、])\s*/, '').trim()
    const row = text.match(/^([^：:\n]{1,90})[：:]\s*(.+)$/)
    if (!row || !/门票|联票|套票|参观票|讲解|珍宝馆|钟表馆/.test(row[1])) continue
    const money = /(?:[¥￥]\s*[\dXx][\dXx,.]*(?:[-—~～][\dXx][\dXx,.]*)?(?:元)?(?:起)?|各?[\dXx][\dXx,.]*(?:[-—~～][\dXx][\dXx,.]*)?元(?:起)?)(?:\s*[/／]\s*(?:人|位|张|份|晚|间|套|单))?/g
    const prices = [...row[2].matchAll(money)]
    // Several prices in one sentence have ambiguous scope; retain them in the full reply.
    if (prices.length !== 1) continue
    const price = prices[0]
    const condition = (row[2].slice(0, price.index) + row[2].slice(price.index! + price[0].length)).trim().replace(/^[，,；;。]+|[。]+$/g, '')
    references.push({ title: row[1].trim(), price: price[0], condition })
  }
  return references
}
