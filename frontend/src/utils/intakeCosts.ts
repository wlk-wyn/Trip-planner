import type { TripCostContext, TripIntakeData } from '../types/index.ts'

/** Preserve explicitly filled fields with older servers, but honor explicit null
 * from the current normalizer so a correction cannot retain a stale total. */
export function applyIntakeCosts(target: Partial<TripCostContext>, intake: Partial<TripIntakeData>) {
  for (const key of ['party_size', 'room_count', 'destination_budget'] as const) {
    if (intake[key] !== undefined) target[key] = intake[key]
  }
  if (intake.budget_mode !== undefined) target.budget_mode = intake.budget_mode
  if (target.budget_mode === 'deferred') target.destination_budget = null
}

export function intakeCostConditions(context: Partial<TripCostContext>): string[] {
  return [
    context.party_size != null ? `同行总人数：${context.party_size}人` : '',
    context.room_count != null ? `每晚房间数：${context.room_count}间` : '',
    context.budget_mode === 'deferred' ? '预算稍后再定，先规划行程' :
      context.destination_budget != null ? `目的地内全体总预算${context.destination_budget}元，不含往返大交通` : '',
  ].filter(Boolean)
}

/** Blank resends and reloads cannot silently bypass a pending budget question. */
export function needsBudgetClarification(context: Partial<TripCostContext>, days: number, pending: boolean): boolean {
  return pending && context.budget_mode !== 'deferred' &&
    (context.party_size == null || context.destination_budget == null || (days > 1 && context.room_count == null))
}
