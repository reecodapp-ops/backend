"""Nightly reconciliation worker.

Calls the schema's ``reconcile_business_cash(business_id)`` function for every
business to correct any drift in cash_on_hand / total_debt_out. The function
recomputes both values from the canonical (non-correction) set of events, so
this is the safety net against any trigger or service bug that ever lets the
cached aggregates wander.

This worker is PostgreSQL-only because the reconcile function lives in the
schema. Running it against SQLite is a no-op (function does not exist).

CLI:
    python -m app.workers.reconciliation
    python -m app.workers.reconciliation --business <uuid>

Scheduling: once a day off-peak is plenty.
    cron:        15 2 * * *      python -m app.workers.reconciliation
    systemd:     OnCalendar=*-*-* 02:15:00
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

LOG = logging.getLogger("dukamate.workers.reconciliation")


async def reconcile_business(
    session: AsyncSession, business_id: uuid.UUID
) -> None:
    bind = session.get_bind()
    if bind is None or bind.dialect.name != "postgresql":
        LOG.warning(
            "reconcile_business_cash is PostgreSQL-only; skipping (%s)",
            bind.dialect.name if bind else "no-bind",
        )
        return
    # reconcile_business_cash uses SECURITY INVOKER and reads from sales,
    # expenses, stock_purchases, customer_payments, capital_transactions —
    # all under RLS. Without app.current_business_id set, RLS hides every row
    # and the function would zero out cash_on_hand to just opening_balance.
    # Set the RLS variable so the function's internal SELECTs see the data.
    await session.execute(
        text("SELECT set_config('app.current_business_id', :bid, true)"),
        {"bid": str(business_id)},
    )
    await session.execute(
        text("SELECT reconcile_business_cash(:bid)"),
        {"bid": str(business_id)},
    )
    await session.commit()


async def run_for_all_businesses() -> None:
    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    started = datetime.now(timezone.utc)
    async with factory() as session:
        ids = (await session.execute(select(Business.id))).scalars().all()
    count = 0
    for bid in ids:
        async with factory() as session:
            try:
                await reconcile_business(session, bid)
                count += 1
                LOG.info("reconciled business=%s", bid)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.error("reconcile failed for business %s: %s", bid, exc)
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    LOG.info("reconciliation done: %d businesses in %.2fs", count, elapsed)


def main() -> int:
    parser = argparse.ArgumentParser(description="DukaMate nightly reconciliation")
    parser.add_argument("--business", type=uuid.UUID, default=None)
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    if args.business is not None:
        async def _one():
            engine = get_engine()
            factory = async_sessionmaker(engine, expire_on_commit=False)
            async with factory() as session:
                await reconcile_business(session, args.business)
                LOG.info("reconciled business=%s", args.business)
        asyncio.run(_one())
    else:
        asyncio.run(run_for_all_businesses())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
