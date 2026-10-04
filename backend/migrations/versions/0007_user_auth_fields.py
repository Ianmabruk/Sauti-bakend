"""Add the missing auth columns expected by the current User model.

The original migration set created a ``users`` table without the email and
password fields that the application now relies on for local sign-up and login.
The live database therefore looked like a partially-initialised app state: the
model and the database schema diverged even though the code path was already in
production.
"""

from alembic import op
import sqlalchemy as sa

revision = "0007_user_auth_fields"
down_revision = "0006_schema_fixes"
branch_labels = None
depends_on = None


def _table_columns(bind, table_name):
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return []
    return [col["name"] for col in inspector.get_columns(table_name)]


def _has_unique(bind, table_name, column_names):
    inspector = sa.inspect(bind)
    if table_name not in inspector.get_table_names():
        return False
    for unique in inspector.get_unique_constraints(table_name):
        if sorted(unique["column_names"] or []) == sorted(column_names):
            return True
    for index in inspector.get_indexes(table_name):
        if index.get("unique") and sorted(index["column_names"] or []) == sorted(column_names):
            return True
    return False


def _duplicate_emails(bind):
    connection = op.get_bind()
    rows = connection.execute(
        sa.text(
            "SELECT email, COUNT(*) AS n FROM users "
            "WHERE email IS NOT NULL AND email != '' "
            "GROUP BY email HAVING COUNT(*) > 1 ORDER BY email"
        )
    ).fetchall()
    return [(row[0], row[1]) for row in rows]


def upgrade():
    bind = op.get_bind()
    if "users" not in sa.inspect(bind).get_table_names():
        return

    columns = set(_table_columns(bind, "users"))

    with op.batch_alter_table("users") as batch_op:
        if "email" not in columns:
            batch_op.add_column(sa.Column("email", sa.String(255), nullable=True))
        if "password_hash" not in columns:
            batch_op.add_column(sa.Column("password_hash", sa.String(255), nullable=True))

    if not _has_unique(bind, "users", ["email"]):
        duplicates = _duplicate_emails(bind)
        if duplicates:
            listed = ", ".join(f"{email!r} x{count}" for email, count in duplicates[:10])
            raise RuntimeError(
                "Cannot add the UNIQUE constraint on users.email: the column "
                f"already holds duplicate values ({listed}"
                f"{' ...' if len(duplicates) > 10 else ''}). Resolve the "
                "duplicates, then re-run this migration. No rows were changed."
            )
        with op.batch_alter_table("users") as batch_op:
            batch_op.create_unique_constraint("uq_users_email", ["email"])


def downgrade():
    bind = op.get_bind()
    if "users" not in sa.inspect(bind).get_table_names():
        return

    columns = set(_table_columns(bind, "users"))

    if _has_unique(bind, "users", ["email"]):
        with op.batch_alter_table("users") as batch_op:
            batch_op.drop_constraint("uq_users_email", type_="unique")

    with op.batch_alter_table("users") as batch_op:
        if "password_hash" in columns:
            batch_op.drop_column("password_hash")
        if "email" in columns:
            batch_op.drop_column("email")
