"""Bound accepted work in the same transaction that inserts a durable job."""

class QueueCapacityError(Exception):
    def __init__(self, message, retry_after=15):
        super().__init__(message)
        self.retry_after = retry_after


def require_capacity(db, owner, settings):
    # The caller holds BEGIN IMMEDIATE: competing processes cannot both admit
    # the final slot. Existing request IDs are handled before this check.
    counts = db.execute("""SELECT count(*) AS total,
        coalesce(sum(CASE WHEN owner=? THEN 1 ELSE 0 END),0) AS own
        FROM planning_runs WHERE status IN ('queued','running')""", (owner,)).fetchone()
    if counts['own'] >= settings.planning_max_pending_per_owner:
        raise QueueCapacityError('已有规划任务在处理，请等待完成，或取消不需要的任务后再试')
    if counts['total'] >= settings.planning_max_pending:
        raise QueueCapacityError('规划服务当前较忙，本次尚未创建任务，请稍后重试')
