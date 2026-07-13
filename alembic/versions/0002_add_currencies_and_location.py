"""add currencies table, businesses.currency_id, structured location

Revision ID: 0002_add_currencies_and_location
Revises: 0001_fix_uuid_generate_v7
Create Date: 2026-07-02

WHAT THIS MIGRATION DOES
-------------------------
1. Creates the ``currencies`` table (ISO 4217 master list) and seeds it with
   every currency listed in dukamate_schema_v3.sql — DukaMate's current/likely
   markets plus the major world currencies. Adding a new currency later is a
   plain INSERT; no further migration is required for that.
2. Adds ``businesses.currency_id`` (nullable at first), backfills it from the
   existing free-text ``businesses.currency_code`` by matching ISO codes
   case-insensitively, then makes it NOT NULL and adds the FK once every row
   is backfilled.
3. Adds the structured location columns to ``businesses``
   (street/city/district/country/formatted_address/latitude/longitude/
   place_id). The legacy ``location`` free-text column is untouched and kept.
4. Adds ``subscription_plans.currency_id`` (nullable, matching the schema —
   a $0 plan need not carry a currency) and backfills it the same way.
5. Adds the supporting indexes from the schema
   (idx_currencies_active, idx_businesses_currency, idx_businesses_geo).

THIS MIGRATION IS NON-DESTRUCTIVE
-----------------------------------
``businesses.currency_code`` / ``currency_symbol`` and
``subscription_plans.currency_code`` are left in place. They become
unused by the application after this deploy (the ORM model no longer maps a
Python column to them — see ``app/models/business.py`` /
``app/models/lookups.py``, which now derive the same values dynamically from
the ``currency`` relationship), but the columns and their data remain in the
database as a safety net until a human has verified the backfill in
production. Dropping them is migration 0003, run as a separate, deliberate
step — see that migration's docstring for the data-loss discussion.

BACKFILL EDGE CASE
--------------------
If a business's ``currency_code`` does not match any seeded ``currencies.iso_code``
(e.g. a typo'd or non-ISO code was previously allowed by the old free-text
validator), that business is backfilled to USD and the id is emitted via
RAISE NOTICE so it can be reviewed and corrected with a normal
``PATCH /businesses/{id}`` call (now validated against the currencies table)
after migration. Given the old schema's regex enforced 3 uppercase letters,
this path should affect zero rows in a healthy database; it exists only as a
safety net.
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = "0002_add_currencies_and_location"
down_revision: Union[str, None] = "0001_fix_uuid_generate_v7"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_CURRENCY_SEED = [
    # (iso_code, name, symbol, decimal_places, country, sort_order)
    ("UGX", "Ugandan Shilling", "USh", 0, "Uganda", 1),
    ("MGA", "Malagasy Ariary", "Ar", 2, "Madagascar", 2),
    ("TZS", "Tanzanian Shilling", "TSh", 2, "Tanzania", 3),
    ("KES", "Kenyan Shilling", "KSh", 2, "Kenya", 4),
    ("RWF", "Rwandan Franc", "FRw", 0, "Rwanda", 5),
    ("NGN", "Nigerian Naira", "\u20a6", 2, "Nigeria", 6),
    ("INR", "Indian Rupee", "\u20b9", 2, "India", 7),
    ("ZAR", "South African Rand", "R", 2, "South Africa", 10),
    ("GHS", "Ghanaian Cedi", "GH\u20b5", 2, "Ghana", 11),
    ("XOF", "West African CFA Franc", "CFA", 0, "West Africa (UEMOA)", 12),
    ("XAF", "Central African CFA Franc", "FCFA", 0, "Central Africa (CEMAC)", 13),
    ("EGP", "Egyptian Pound", "E\u00a3", 2, "Egypt", 14),
    ("ETB", "Ethiopian Birr", "Br", 2, "Ethiopia", 15),
    ("ZMW", "Zambian Kwacha", "ZK", 2, "Zambia", 16),
    ("MWK", "Malawian Kwacha", "MK", 2, "Malawi", 17),
    ("BWP", "Botswana Pula", "P", 2, "Botswana", 18),
    ("MZN", "Mozambican Metical", "MT", 2, "Mozambique", 19),
    ("CDF", "Congolese Franc", "FC", 2, "DR Congo", 20),
    ("SSP", "South Sudanese Pound", "SSP", 2, "South Sudan", 21),
    ("SOS", "Somali Shilling", "Sh", 2, "Somalia", 22),
    ("DZD", "Algerian Dinar", "DA", 2, "Algeria", 23),
    ("MAD", "Moroccan Dirham", "DH", 2, "Morocco", 24),
    ("TND", "Tunisian Dinar", "DT", 3, "Tunisia", 25),
    ("LYD", "Libyan Dinar", "LD", 3, "Libya", 26),
    ("AOA", "Angolan Kwanza", "Kz", 2, "Angola", 27),
    ("BIF", "Burundian Franc", "FBu", 0, "Burundi", 28),
    ("DJF", "Djiboutian Franc", "Fdj", 0, "Djibouti", 29),
    ("GMD", "Gambian Dalasi", "D", 2, "Gambia", 30),
    ("GNF", "Guinean Franc", "FG", 0, "Guinea", 31),
    ("LRD", "Liberian Dollar", "L$", 2, "Liberia", 32),
    ("LSL", "Lesotho Loti", "L", 2, "Lesotho", 33),
    ("NAD", "Namibian Dollar", "N$", 2, "Namibia", 34),
    ("SCR", "Seychellois Rupee", "SR", 2, "Seychelles", 35),
    ("SLE", "Sierra Leonean Leone", "Le", 2, "Sierra Leone", 36),
    ("SZL", "Swazi Lilangeni", "L", 2, "Eswatini", 37),
    ("CVE", "Cape Verdean Escudo", "$", 2, "Cape Verde", 38),
    ("STN", "S\u00e3o Tom\u00e9 and Pr\u00edncipe Dobra", "Db", 2, "S\u00e3o Tom\u00e9 and Pr\u00edncipe", 39),
    ("KMF", "Comorian Franc", "CF", 0, "Comoros", 40),
    ("USD", "US Dollar", "$", 2, "United States", 50),
    ("EUR", "Euro", "\u20ac", 2, "Eurozone", 51),
    ("GBP", "British Pound", "\u00a3", 2, "United Kingdom", 52),
    ("CNY", "Chinese Yuan", "\u00a5", 2, "China", 53),
    ("JPY", "Japanese Yen", "\u00a5", 0, "Japan", 54),
    ("CHF", "Swiss Franc", "CHF", 2, "Switzerland", 55),
    ("CAD", "Canadian Dollar", "C$", 2, "Canada", 56),
    ("AUD", "Australian Dollar", "A$", 2, "Australia", 57),
    ("AED", "UAE Dirham", "\u062f.\u0625", 2, "United Arab Emirates", 58),
    ("SAR", "Saudi Riyal", "\ufdfc", 2, "Saudi Arabia", 59),
    ("QAR", "Qatari Riyal", "\ufdfc", 2, "Qatar", 60),
    ("TRY", "Turkish Lira", "\u20ba", 2, "Turkey", 61),
    ("BRL", "Brazilian Real", "R$", 2, "Brazil", 62),
    ("PKR", "Pakistani Rupee", "\u20a8", 2, "Pakistan", 63),
    ("BDT", "Bangladeshi Taka", "\u09f3", 2, "Bangladesh", 64),
    ("IDR", "Indonesian Rupiah", "Rp", 2, "Indonesia", 65),
    ("PHP", "Philippine Peso", "\u20b1", 2, "Philippines", 66),
    ("VND", "Vietnamese Dong", "\u20ab", 0, "Vietnam", 67),
    ("KRW", "South Korean Won", "\u20a9", 0, "South Korea", 68),
]


def upgrade() -> None:
    # ----- 1. currencies table -----------------------------------------------
    op.create_table(
        "currencies",
        sa.Column("id", sa.SmallInteger(), primary_key=True, autoincrement=True),
        sa.Column("iso_code", sa.String(3), nullable=False, unique=True),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("symbol", sa.String(10), nullable=False),
        sa.Column("decimal_places", sa.SmallInteger(), nullable=False, server_default="2"),
        sa.Column("country", sa.String(100), nullable=True),
        sa.Column("exchange_rate_to_usd", sa.Numeric(18, 6), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("sort_order", sa.SmallInteger(), nullable=False, server_default="0"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
    )
    op.create_index("idx_currencies_active", "currencies", ["is_active"])

    currencies_tbl = sa.table(
        "currencies",
        sa.column("iso_code", sa.String),
        sa.column("name", sa.String),
        sa.column("symbol", sa.String),
        sa.column("decimal_places", sa.SmallInteger),
        sa.column("country", sa.String),
        sa.column("sort_order", sa.SmallInteger),
    )
    op.bulk_insert(
        currencies_tbl,
        [
            {
                "iso_code": iso,
                "name": name,
                "symbol": symbol,
                "decimal_places": dp,
                "country": country,
                "sort_order": sort_order,
            }
            for iso, name, symbol, dp, country, sort_order in _CURRENCY_SEED
        ],
    )

    op.execute(
        sa.text(
            "CREATE TRIGGER set_updated_at BEFORE UPDATE ON currencies "
            "FOR EACH ROW EXECUTE FUNCTION set_updated_at()"
        )
    )

    # ----- 2. businesses: currency_id + backfill ------------------------------
    op.add_column("businesses", sa.Column("currency_id", sa.SmallInteger(), nullable=True))

    op.execute(
        sa.text(
            """
            UPDATE businesses b
            SET currency_id = c.id
            FROM currencies c
            WHERE UPPER(b.currency_code) = c.iso_code
            """
        )
    )
    # Safety-net fallback for any code that didn't match a seeded currency.
    op.execute(
        sa.text(
            """
            UPDATE businesses
            SET currency_id = (SELECT id FROM currencies WHERE iso_code = 'USD')
            WHERE currency_id IS NULL
            """
        )
    )
    op.execute(
        sa.text(
            """
            DO $$
            DECLARE v_count INT;
            BEGIN
                SELECT COUNT(*) INTO v_count FROM businesses WHERE currency_code IS DISTINCT FROM
                    (SELECT iso_code FROM currencies WHERE id = businesses.currency_id);
                IF v_count > 0 THEN
                    RAISE NOTICE
                        '% business row(s) had a currency_code with no matching '
                        'currencies.iso_code and were defaulted to USD — review and '
                        'correct via PATCH /businesses/{id}.', v_count;
                END IF;
            END $$;
            """
        )
    )

    op.alter_column("businesses", "currency_id", nullable=False)
    op.create_foreign_key(
        "fk_businesses_currency", "businesses", "currencies", ["currency_id"], ["id"]
    )
    op.create_index("idx_businesses_currency", "businesses", ["currency_id"])

    # ----- 3. businesses: structured location (purely additive) --------------
    op.add_column("businesses", sa.Column("street", sa.String(200), nullable=True))
    op.add_column("businesses", sa.Column("city", sa.String(100), nullable=True))
    op.add_column("businesses", sa.Column("district", sa.String(100), nullable=True))
    op.add_column("businesses", sa.Column("country", sa.String(100), nullable=True))
    op.add_column("businesses", sa.Column("formatted_address", sa.String(400), nullable=True))
    op.add_column("businesses", sa.Column("latitude", sa.Numeric(10, 7), nullable=True))
    op.add_column("businesses", sa.Column("longitude", sa.Numeric(10, 7), nullable=True))
    op.add_column("businesses", sa.Column("place_id", sa.String(255), nullable=True))
    op.create_index(
        "idx_businesses_geo",
        "businesses",
        ["latitude", "longitude"],
        postgresql_where=sa.text("latitude IS NOT NULL AND longitude IS NOT NULL"),
    )

    # ----- 4. subscription_plans: currency_id + backfill ----------------------
    op.add_column("subscription_plans", sa.Column("currency_id", sa.SmallInteger(), nullable=True))
    op.execute(
        sa.text(
            """
            UPDATE subscription_plans sp
            SET currency_id = c.id
            FROM currencies c
            WHERE UPPER(sp.currency_code) = c.iso_code
            """
        )
    )
    op.create_foreign_key(
        "fk_subscription_plans_currency",
        "subscription_plans",
        "currencies",
        ["currency_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint(
        "fk_subscription_plans_currency", "subscription_plans", type_="foreignkey"
    )
    op.drop_column("subscription_plans", "currency_id")

    op.drop_index("idx_businesses_geo", table_name="businesses")
    op.drop_column("businesses", "place_id")
    op.drop_column("businesses", "longitude")
    op.drop_column("businesses", "latitude")
    op.drop_column("businesses", "formatted_address")
    op.drop_column("businesses", "country")
    op.drop_column("businesses", "district")
    op.drop_column("businesses", "city")
    op.drop_column("businesses", "street")

    op.drop_index("idx_businesses_currency", table_name="businesses")
    op.drop_constraint("fk_businesses_currency", "businesses", type_="foreignkey")
    op.drop_column("businesses", "currency_id")

    op.execute(sa.text("DROP TRIGGER IF EXISTS set_updated_at ON currencies"))
    op.drop_index("idx_currencies_active", table_name="currencies")
    op.drop_table("currencies")
