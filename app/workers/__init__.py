"""Background worker entrypoints.

Two jobs:
  - issue_engine.run_for_all_businesses(): recomputes ``issues`` for every
    business by running the Decision Engine rules.
  - reconciliation.run_for_all_businesses(): calls the schema's
    ``reconcile_business_cash`` function for every business to correct any
    drift in cash_on_hand / total_debt_out.

Both can be invoked as CLI entrypoints (``python -m app.workers.issue_engine``
and ``python -m app.workers.reconciliation``) so they integrate with any
scheduler — cron, systemd timers, k8s CronJobs, arq, Celery beat.

They are intentionally NOT scheduled in-process; running them from a separate
process gives a clean operational boundary and avoids tying worker lifetime to
API uptime.
"""
