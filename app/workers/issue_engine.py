"""Issue Engine worker.

Iterates every business and runs ``recompute_for_business`` per business. Logs a
created/superseded/unchanged summary line per business so the run is observable
in cron/systemd logs without extra tooling.

CLI:
    python -m app.workers.issue_engine
    python -m app.workers.issue_engine --business <uuid>   # single business

Scheduling:
    cron:        */15 * * * *   python -m app.workers.issue_engine
    systemd:     OnCalendar=*:0/15  (every 15 minutes)
    k8s:         a CronJob image running the same entrypoint
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
from app.services.issue_service import recompute_for_business

LOG = logging.getLogger("dukamate.workers.issue_engine")


async def run_for_business(
    session: AsyncSession, business_id: uuid.UUID
) -> tuple[int, int, int]:
    # RLS: set the session variable so policies that filter on
    # app.current_business_id let us read this business's data.
    bind = session.get_bind()
    if bind is not None and bind.dialect.name == "postgresql":
        await session.execute(
            text("SELECT set_config('app.current_business_id', :bid, true)"),
            {"bid": str(business_id)},
        )
    result = await recompute_for_business(session, business_id)
    await session.commit()
    return result.created, result.superseded, result.unchanged


async def run_for_all_businesses() -> None:
    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    total = {"created": 0, "superseded": 0, "unchanged": 0}
    business_count = 0
    started = datetime.now(timezone.utc)
    async with factory() as session:
        business_ids = (
            await session.execute(select(Business.id))
        ).scalars().all()
    for bid in business_ids:
        # New session per business: RLS context is set per-transaction, and a
        # crash on one business mustn't leak its setting to the next.
        async with factory() as session:
            try:
                created, superseded, unchanged = await run_for_business(session, bid)
            except Exception as exc:  # pragma: no cover - defensive
                LOG.error("recompute failed for business %s: %s", bid, exc)
                continue
        total["created"] += created
        total["superseded"] += superseded
        total["unchanged"] += unchanged
        business_count += 1
        LOG.info(
            "business=%s created=%d superseded=%d unchanged=%d",
            bid, created, superseded, unchanged,
        )
    elapsed = (datetime.now(timezone.utc) - started).total_seconds()
    LOG.info(
        "issue_engine done: %d businesses in %.2fs | total created=%d superseded=%d unchanged=%d",
        business_count, elapsed, total["created"], total["superseded"], total["unchanged"],
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="DukaMate Issue Engine worker")
    parser.add_argument(
        "--business",
        type=uuid.UUID,
        default=None,
        help="Run for a single business id (skip the all-businesses sweep).",
    )
    parser.add_argument(
        "--log-level", default="INFO", help="Python log level (default: INFO)"
    )
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
                created, superseded, unchanged = await run_for_business(
                    session, args.business
                )
                LOG.info(
                    "business=%s created=%d superseded=%d unchanged=%d",
                    args.business, created, superseded, unchanged,
                )
        asyncio.run(_one())
    else:
        asyncio.run(run_for_all_businesses())
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
