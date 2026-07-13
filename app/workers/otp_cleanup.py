"""OTP cleanup worker.

Deletes used and/or expired ``verification_codes`` rows older than a cutoff so
the table stays small. Used codes are kept for ``--retain-days`` (default 7)
for forensics; after that they're gone.

CLI:
    python -m app.workers.otp_cleanup
    python -m app.workers.otp_cleanup --retain-days 7

Scheduling: once a day is plenty.
    cron:    30 2 * * *   python -m app.workers.otp_cleanup
    systemd: OnCalendar=*-*-* 02:30:00
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import async_sessionmaker

from app.core.database import get_engine
from app.repositories.verification_code_repository import (
    VerificationCodeRepository,
)

LOG = logging.getLogger("dukamate.workers.otp_cleanup")


async def run(retain_days: int) -> int:
    engine = get_engine()
    factory = async_sessionmaker(engine, expire_on_commit=False)
    cutoff = datetime.now(timezone.utc) - timedelta(days=retain_days)
    async with factory() as session:
        deleted = await VerificationCodeRepository(
            session
        ).delete_expired_or_used_older_than(cutoff)
        await session.commit()
    LOG.info("otp_cleanup deleted %d rows older than %s", deleted, cutoff.isoformat())
    return deleted


def main() -> int:
    parser = argparse.ArgumentParser(description="DukaMate OTP cleanup")
    parser.add_argument(
        "--retain-days",
        type=int,
        default=7,
        help="Keep used / expired OTP rows for this many days before deletion.",
    )
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args()
    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    asyncio.run(run(args.retain_days))
    return 0


if __name__ == "__main__":  # pragma: no cover - CLI entry
    sys.exit(main())
