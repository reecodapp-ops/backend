"""email + OTP authentication

Restructures the users table and introduces verification_codes for the
email-OTP flow. The old phone-based ``users`` schema is reshaped in place:

  - email added (NOT NULL UNIQUE)
  - phone_number relaxed to NULL (kept on the table for future SMS use)
  - password_hash relaxed to NULL (set later via /auth/set-password)
  - full_name made NOT NULL
  - is_verified renamed/split into is_email_verified + is_phone_verified

verification_codes is added fresh.

Devices stays as-is because sync_events still references it via a nullable FK;
the new auth flow simply doesn't write device rows anymore.

Revision ID: 0009_email_auth
Revises: 0008_issues_sync
Create Date: 2026-06-04
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009_email_auth"
down_revision: Union[str, None] = "0008_issues_sync"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ----- users: reshape -------------------------------------------------- #
    # 1. Add email as nullable first, then backfill, then enforce NOT NULL +
    #    UNIQUE. For brand-new installs (no rows) this is fine; for existing
    #    data, operators need to provide email values out-of-band.
    op.add_column("users", sa.Column("email", sa.String(length=255), nullable=True))

    # 2. Add the verification flags. Default FALSE preserves the conservative
    #    behaviour: every existing row is treated as unverified until the
    #    operator confirms otherwise.
    op.add_column(
        "users",
        sa.Column(
            "is_email_verified",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "is_phone_verified",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
    )

    # 3. The old is_verified column (if present) is the closest equivalent to
    #    is_phone_verified under the prior phone-based flow. Copy it over.
    op.execute(
        "UPDATE users SET is_phone_verified = COALESCE(is_verified, FALSE)"
    )
    op.drop_column("users", "is_verified")

    # 4. password_hash becomes nullable (set later in the new flow).
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=True,
    )

    # 5. phone_number was NOT NULL UNIQUE. Drop UNIQUE and relax NOT NULL so
    #    that brand-new users can register without a phone number.
    op.drop_constraint("users_phone_number_key", "users", type_="unique")
    op.alter_column(
        "users",
        "phone_number",
        existing_type=sa.String(length=20),
        nullable=True,
    )

    # 6. full_name was nullable; now required.
    op.execute("UPDATE users SET full_name = COALESCE(full_name, email, 'User')")
    op.alter_column(
        "users",
        "full_name",
        existing_type=sa.String(length=150),
        nullable=False,
    )

    # 7. email NOT NULL + UNIQUE. Skipped if a deployment must seed values
    #    first; the operator-facing data-fix step happens between adding the
    #    column (step 1) and this constraint set.
    op.alter_column(
        "users",
        "email",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_unique_constraint("users_email_key", "users", ["email"])

    # ----- verification_codes -------------------------------------------- #
    op.create_table(
        "verification_codes",
        sa.Column(
            "id",
            postgresql.UUID(as_uuid=True),
            server_default=sa.text("gen_random_uuid()"),
            nullable=False,
        ),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("code", sa.String(length=10), nullable=False),
        sa.Column("verification_type", sa.String(length=20), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("NOW()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.CheckConstraint(
            "verification_type IN ('EMAIL_VERIFICATION','PASSWORD_RESET','EMAIL_CHANGE')",
            name="verification_codes_type_check",
        ),
    )
    op.create_index(
        "idx_verification_codes_user_type",
        "verification_codes",
        ["user_id", "verification_type", "created_at"],
    )
    op.create_index(
        "idx_verification_codes_active",
        "verification_codes",
        ["user_id", "verification_type"],
        postgresql_where=sa.text("used_at IS NULL"),
    )


def downgrade() -> None:
    op.drop_index("idx_verification_codes_active", table_name="verification_codes")
    op.drop_index("idx_verification_codes_user_type", table_name="verification_codes")
    op.drop_table("verification_codes")

    op.drop_constraint("users_email_key", "users", type_="unique")
    op.alter_column("users", "full_name", existing_type=sa.String(length=150), nullable=True)
    op.alter_column(
        "users",
        "phone_number",
        existing_type=sa.String(length=20),
        nullable=False,
    )
    op.create_unique_constraint("users_phone_number_key", "users", ["phone_number"])
    op.alter_column(
        "users",
        "password_hash",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.add_column(
        "users",
        sa.Column(
            "is_verified",
            sa.Boolean(),
            server_default=sa.text("FALSE"),
            nullable=False,
        ),
    )
    op.execute("UPDATE users SET is_verified = is_phone_verified")
    op.drop_column("users", "is_phone_verified")
    op.drop_column("users", "is_email_verified")
    op.drop_column("users", "email")
