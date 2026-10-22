/** Keep confirmed HTTP rejection distinct from a lost response after submission. */
export class TripServiceError extends Error {
  readonly status?: number
  readonly retryAfter?: number
  constructor(message: string, status?: number, retryAfter?: number) { super(message); this.name='TripServiceError'; this.status=status; this.retryAfter=retryAfter }
}
export const submissionRejected = (error: unknown, accepted = false) => !accepted && error instanceof TripServiceError && [400, 401, 403, 404, 409, 422, 429].includes(error.status || 0)
export const runMissing = (error: unknown) => error instanceof TripServiceError && [404, 410].includes(error.status || 0)
export const runWatchRejected = (error: unknown) => error instanceof TripServiceError && [401, 403, 404, 410].includes(error.status || 0)
