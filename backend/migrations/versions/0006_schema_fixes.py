"""Align three columns with their models, and add the vendor token secret.

Migration 0005 brought the four Paystack tables in line with their models, and the
autogenerate drift check then reported two remaining differences. Both were
pre-existing, and both were real bugs rather than cosmetic.

``intents`` had the wrong primary key
------------------------------------

``0001_initial`` declares ``PrimaryKeyConstraint("name")`` on ``intents``, while
the model declares ``id`` as the primary key and ``name`` merely unique. Every
other table in this schema uses a surrogate ``id``, so the constraint in 0001 is a
copy-paste slip rather than a decision.

It did not stay harmless. ``db.create_all()`` builds tables from the *model*, so
development and the test suite got ``intents`` keyed on ``id`` while any database
built from the migrations got it keyed on ``name``. Two different schemas under
one model means a query that works locally can fail in production for reasons that
have nothing to do with the code that changed. This moves the primary key to
``id``, matching the model and every other table.

``users.phone_number`` was not unique
-------------------------------------

The model declares ``unique=True``; no migration ever created the constraint. A
duplicate phone number was therefore accepted by the database while the model
claimed otherwise — an application that looks unique and is not, which for an
identity column is the worst shape a constraint can be in.

Existing duplicate values are reported rather than deleted. Silently nulling or
de-duplicating someone's identity column to satisfy a new constraint is not a
decision this migration gets to make on its own; the upgrade fails with the
offending rows named instead.

``users.vendor_token_hash``
---------------------------

Stores a hash of the secret that authorises vendor-token issuance for that user.
See :mod:`backend.payments.auth`. Nullable, so an existing user is issued their
secret on the next call rather than being locked out by this deploy.

The order matters: the phone-number constraint is added last so that a duplicate
does not abort the transaction before the additive changes have been committed.
"""
from alembic import op
import sqlalchemy as sa

revision = "0006_schema_fixes"
down_revision = "0005_paystack_subscriptions"
branch_labels = None
depends_on = None


def _table(name):
    return sa.table(
        name,
        sa.column("id", sa.String()),
        sa.column("name", sa.String()),
        sa.column("phone_number", sa.String()),
    )


def _has_unique(bind, table_name, column_names):
    """Whether a UNIQUE constraint/index already covers these columns."""
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    for unique in inspector.get_unique_constraints(table_name):
        if sorted(unique["column_names"] or []) == sorted(column_names):
            return True
    for index in inspector.get_indexes(table_name):
        if index.get("unique") and sorted(
            index["column_names"] or []
        ) == sorted(column_names):
            return True
    return False


def _reflect_intents(bind):
    """Reflect ``intents`` with a naming convention applied.

    SQLite implements a primary key as an anonymous autoindex, so the reflected
    constraint has no name and ``drop_constraint("pk_intents")`` fails with
    "No such constraint". Reflecting through a naming convention assigns the
    conventional name to the constraint that is already there, which is what
    makes it droppable — on both SQLite and PostgreSQL.
    """
    meta = sa.MetaData(naming_convention={"pk": "pk_intents"})
    return sa.Table("intents", meta, autoload_with=bind)


def _duplicate_phone_numbers(bind):
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT phone_number, COUNT(*) AS n FROM users "
            "WHERE phone_number IS NOT NULL AND phone_number != '' "
            "GROUP BY phone_number HAVING COUNT(*) > 1 ORDER BY phone_number"
        )
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def upgrade():
    bind = op.get_bind()

    # --- intents: move the primary key from name to id ---------------------
    inspector = sa.inspect(bind)
    if "intents" in inspector.get_table_names():
        pk_columns = list(
            (inspector.get_pk_constraint("intents") or {}).get("constrained_columns")
            or []
        )
        if pk_columns == ["name"]:
            # id is NOT NULL but not unique, so in principle duplicates could
            # exist. Give any that do a fresh id before it has to be unique.
            duplicates = bind.execute(
                sa.text(
                    "SELECT id FROM intents WHERE id IS NOT NULL "
                    "GROUP BY id HAVING COUNT(*) > 1"
                )
            ).fetchall()
            for (duplicated_id,) in duplicates:
                rows = bind.execute(
                    sa.text("SELECT name FROM intents WHERE id = :id"),
                    {"id": duplicated_id},
                ).fetchall()
                for index, (name,) in enumerate(rows):
                    if index == 0:
                        continue  # the first keeps the id
                    bind.execute(
                        sa.text("UPDATE intents SET id = :new WHERE name = :name"),
                        {"new": f"intent-{duplicated_id}-{index}", "name": name},
                    )

            with op.batch_alter_table(
                "intents",
                copy_from=_reflect_intents(bind),
                naming_convention={"pk": "pk_intents"},
            ) as batch_op:
                batch_op.drop_constraint("pk_intents", type_="primary")
                batch_op.create_primary_key("pk_intents", ["id"])

        if not _has_unique(bind, "intents", ["name"]):
            with op.batch_alter_table("intents") as batch_op:
                batch_op.create_unique_constraint("uq_intents_name", ["name"])

    # --- users: add the vendor token secret --------------------------------
    if "users" in sa.inspect(bind).get_table_names():
        with op.batch_alter_table("users") as batch_op:
            batch_op.add_column(
                sa.Column("vendor_token_hash", sa.String(64), nullable=True)
            )

    # --- users: phone_number uniqueness ------------------------------------
    if "users" in sa.inspect(bind).get_table_names() and not _has_unique(
        bind, "users", ["phone_number"]
    ):
        duplicates = _duplicate_phone_numbers(bind)
        if duplicates:
            listed = ", ".join(
                f"{number!r} x{count}" for number, count in duplicates[:10]
            )
            raise RuntimeError(
                "Cannot add the UNIQUE constraint on users.phone_number: the "
                f"column already holds duplicate values ({listed}"
                f"{' ...' if len(duplicates) > 10 else ''}). Resolve the "
                "duplicates, then re-run this migration. No rows were changed."
            )
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_unique_constraint("uq_users_phone_number", ["phone_number"])


def downgrade():
    bind = op.get_bind()

    if "users" in sa.inspect(bind).get_table_names():
        if _has_unique(bind, "users", ["phone_number"]):
            with op.batch_alter_table("users") as batch_op:
                batch_op.drop_constraint("uq_users_phone_number", type_="unique")
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_column("vendor_token_hash")

    if "intents" in sa.inspect(bind).get_table_names():
        if _has_unique(bind, "intents", ["name"]):
            with op.batch_alter_table("intents") as batch_op:
                batch_op.drop_constraint("uq_intents_name", type_="unique")
        inspector = sa.inspect(bind)
        pk_columns = list(
            (inspector.get_pk_constraint("intents") or {}).get("constrained_columns")
            or []
        )
        if pk_columns == ["id"]:
            with op.batch_alter_table(
                "intents",
                copy_from=_reflect_intents(bind),
                naming_convention={"pk": "pk_intents"},
            ) as batch_op:
                batch_op.drop_constraint("pk_intents", type_="primary")
                batch_op.create_primary_key("pk_intents", ["name"])