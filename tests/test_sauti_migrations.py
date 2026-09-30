"""SAUTI schema migration safety.

The SAUTI memory migration must be additive: it may only create tables and
must never touch existing SautiPay data.
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def run_alembic(db_path: str, target: str) -> None:
    """Run an Alembic upgrade against a scratch SQLite file."""
    result = subprocess.run(
        [
            sys.executable, "-m", "alembic", "upgrade", target,
        ],
        cwd=REPO_ROOT,
        env={
            "DATABASE_URL": f"sqlite:///{db_path}",
            "PATH": "/usr/bin:/bin:/usr/local/bin",
            "HOME": str(REPO_ROOT),
        },
        capture_output=True,
        text=True,
        timeout=180,
    )
    assert result.returncode == 0, result.stderr[-2000:]


class TestSautiMigration:
    def test_migration_chain_applies_to_empty_database(self, tmp_path):
        db_file = tmp_path / "fresh.db"
        run_alembic(str(db_file), "head")

        import sqlite3

        connection = sqlite3.connect(db_file)
        tables = {
            row[0]
            for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        connection.close()

        for expected in (
            "users", "conversations", "messages", "languages", "intents",
            "knowledge_sources", "documents", "document_chunks", "retrieval_records",
            "media_assets", "hero_slides", "categories", "stories",
            "quick_actions", "featured_content", "memory_items",
        ):
            assert expected in tables, f"missing table {expected}"

    def test_existing_data_survives_the_sauti_migration(self, tmp_path):
        """Seed pre-migration data, upgrade, and confirm nothing is lost."""
        db_file = tmp_path / "existing.db"
        run_alembic(str(db_file), "0002_cms_media")

        import sqlite3

        connection = sqlite3.connect(db_file)
        connection.execute(
            "INSERT INTO users (id, phone_number, name, is_active) "
            "VALUES ('u1', '+254700000001', 'Legacy User', 1)"
        )
        connection.execute(
            "INSERT INTO conversations (id, user_id, is_active) VALUES ('c1', 'u1', 1)"
        )
        connection.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('m1', 'c1', 'user', 'Precious legacy message')"
        )
        connection.execute(
            "INSERT INTO knowledge_sources (id, name, is_trusted) "
            "VALUES ('k1', 'Legacy Source', 1)"
        )
        connection.execute(
            "INSERT INTO categories (id, title, accent, is_published, is_active, sort_order) "
            "VALUES ('cat1', 'News', '#e2683a', 1, 1, 0)"
        )
        connection.commit()
        connection.close()

        run_alembic(str(db_file), "head")

        connection = sqlite3.connect(db_file)
        users = connection.execute("SELECT name FROM users").fetchone()[0]
        messages = connection.execute("SELECT content FROM messages").fetchone()[0]
        sources = connection.execute("SELECT COUNT(*) FROM knowledge_sources").fetchone()[0]
        categories = connection.execute("SELECT COUNT(*) FROM categories").fetchone()[0]
        memory_exists = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='memory_items'"
        ).fetchone()
        connection.close()

        assert users == "Legacy User"
        assert messages == "Precious legacy message"
        assert sources == 1
        assert categories == 1
        assert memory_exists is not None


class TestMarketplaceMigration:
    """0004_marketplace must be additive and preserve everything before it."""

    def test_full_chain_applies(self, tmp_path):
        db_file = tmp_path / "chain.db"
        run_alembic(str(db_file), "head")

        import sqlite3

        connection = sqlite3.connect(db_file)
        tables = {
            row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        connection.close()

        for expected in (
            "users", "conversations", "messages", "memory_items",
            "vendors", "products", "marketplace_categories", "news_articles",
            "orders", "transactions", "search_events",
        ):
            assert expected in tables, f"missing {expected}"

    def test_marketplace_migration_preserves_existing_data(self, tmp_path):
        db_file = tmp_path / "preserve.db"
        run_alembic(str(db_file), "0003_sauti_memory")

        import sqlite3

        connection = sqlite3.connect(db_file)
        connection.execute(
            "INSERT INTO users (id, phone_number, name, is_active) "
            "VALUES ('u1', '+254700000009', 'Pre Existing', 1)"
        )
        connection.execute(
            "INSERT INTO conversations (id, user_id, is_active) VALUES ('c1', 'u1', 1)"
        )
        connection.execute(
            "INSERT INTO messages (id, conversation_id, role, content) "
            "VALUES ('m1', 'c1', 'user', 'Original message')"
        )
        connection.execute(
            "INSERT INTO memory_items (id, category, content) "
            "VALUES ('mem1', 'preference', 'Likes Kiswahili')"
        )
        connection.commit()
        connection.close()

        run_alembic(str(db_file), "head")

        connection = sqlite3.connect(db_file)
        user = connection.execute("SELECT name FROM users").fetchone()[0]
        message = connection.execute("SELECT content FROM messages").fetchone()[0]
        memory = connection.execute("SELECT content FROM memory_items").fetchone()[0]
        vendor_table = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='vendors'"
        ).fetchone()
        connection.close()

        assert user == "Pre Existing"
        assert message == "Original message"
        assert memory == "Likes Kiswahili"
        assert vendor_table is not None
