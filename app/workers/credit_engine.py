"""Nightly Customer Credibility Engine worker [NEW v3].

Recomputes every active customer's credit score so scores age even without new
sales/payments (e.g. an open debt crossing the 30/60/120-day thresholds).

RLS CAVEAT (same bug class as reconciliation.py, see migration notes)
----------------------------------------------------------------------
``recompute_customer_credit_score(customer_id)`` is ``SECURITY INVOKER`` and
reads from ``customer_debt_ledger`` — an RLS-protected table. Without
``app.current_business_id`` set for the calling session, RLS hides every row
and the function would silently compute every customer as if they had no debt
history at all (UNSCORED), exactly the class of bug documented for
``reconcile_business_cash`` (SECURITY INVOKER without RLS context set). This
worker therefore loops **per business**, setting the RLS session variable
before calling the per-customer function for that business's customers —
mirroring ``reconciliation.py`` rather than calling the schema's
``recompute_all_credit_scores()`` (which loops globally and would need to be
``SECURITY DEFINER`` to be safe under RLS; see the migration notes for why we
did not change that function's security model in this pass).

CLI:
    python -m app.workers.credit_engine
    python -m app.workers.credit_engine --business <uuid>

Scheduling: after the nightly reconciliation job.
    cron:        30 3 * * *      python -m app.workers.credit_engine
    systemd:     OnCalendar=*-*-* 03:30:00
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
import uuid
from datetime import datetime, timezone

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.database import get_engine
from app.models.business import Business
from app.models.customer import Customer

LOG = logging.getLogger("dukamate.workers.credit_engine")


async def recompute_business_credit_scores(
    session: AsyncSession, business_id: uuid.UUID
) -> int:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        LOG.warning(
            "recompute_customer_credit_score is PostgreSQL-only; skipping (%s)",
            bind.dialect.name if bind else "no-bind",
        )
        return 0

    # Set RLS context BEFORE reading customers/ledger rows for this business —
    # see module docstring for why this is required.
    await session.execute(
        text("SELECT set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )

    customer_ids = (
        await session.execute(
            select(Customer.id).where(
                Customer.business_id == business_id, Customer.status == "ACTIVE"
            )
        )
    ).scalars().all()

    for customer_id in customer_ids:
        await session.execute(
            text("SELECT recompute_customer_credit_score(:cid)"),
            {"cid": str(customer_id)},
        )

    await session.commit()
    return len(customer_ids)


async def run_for_all_businesses() -> None:
    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = datetime.now(timezone.utc)
    async with factory() as session:
        ids = (
            await session.execute(
                select(Business.id).where(Business.status == "ACTIVE")
            )
        ).scalars().all()

    total_customers = 0
    count = 0
    for bid in ids:
        async with factory() as session:
            try:
                n = await recompute_business_credit_scores(session, bid)
                total_customers += n
                count += 1
                LOG.info("business=%s customers_rescored=%d", bid, n)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.error("credit recompute failed for business %s: %s", bid, exc)

    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    LOG.info(
        "credit_engine done: %d businesses, %d customers rescored, in %.2fs",
        count, total_customers, elapsed,
    )


def main() -> int:
    parser = argparse.ArgumentParser(
        description="DukaMate nightly Customer Credibility Engine recompute"
    )
    parser.add_argument("--business", type=uuid.UUID, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.business is not None:
        async def _one() -> None:
            engine = get_engine()
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                n = await recompute_business_credit_scores(session, args.business)
                LOG.info("business=%s customers_rescored=%d", args.business, n)
        asyncio.run(_one())
    else:
        asyncio.run(run_for_all_businesses())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
