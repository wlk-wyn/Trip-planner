import { TripServiceError } from './serviceError.ts'

export function httpFailure(status: number | undefined, detail: unknown, retryHeader: unknown) {
  const seconds = typeof retryHeader === 'string' && /^\d+$/.test(retryHeader) ? Number(retryHeader) : NaN
  const retryAfter = Number.isSafeInteger(seconds) && seconds > 0 ? seconds : undefined
  const base = typeof detail === 'string' && detail ? detail : '服务暂时无法完成此操作，请稍后重试'
  return new TripServiceError(status === 429 && retryAfter ? `${base} 请至少等待 ${retryAfter} 秒再试。` : base, status, retryAfter)
}

export async function readApiJson(response: Response): Promise<any> {
  let data: any
  try { data = await response.json() }
  catch { throw httpFailure(response.status, '服务返回了无法读取的响应，请稍后重试', response.headers.get('Retry-After')) }
  if (!response.ok) throw httpFailure(response.status, data?.detail || data?.message, response.headers.get('Retry-After'))
  return data
}
