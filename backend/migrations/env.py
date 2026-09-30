"""Alembic env for SautiPay."""
import os
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from backend.config import Config

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Set database URL from environment
config.set_main_option("sqlalchemy.url", Config.DATABASE_URL)

# Import models so Alembic can detect them
from backend.models import (  # noqa: E402
    Category,
    Conversation,
    Document,
    DocumentChunk,
    FeaturedContent,
    HeroSlide,
    Intent,
    KnowledgeSource,
    Language,
    MediaAsset,
    Message,
    QuickAction,
    RetrievalRecord,
    Story,
    User,
)
from backend.memory.models import MemoryItem  # noqa: E402
from backend.marketplace.models import (  # noqa: E402
    MarketplaceCategory,
    NewsArticle,
    Order,
    OrderItem,
    Product,
    Review,
    SavedItem,
    SearchEvent,
    Transaction,
    Vendor,
    VendorVerification,
)

target_metadata = [
    User.__table__,
    Conversation.__table__,
    Message.__table__,
    Language.__table__,
    Intent.__table__,
    KnowledgeSource.__table__,
    Document.__table__,
    DocumentChunk.__table__,
    RetrievalRecord.__table__,
    MediaAsset.__table__,
    HeroSlide.__table__,
    Category.__table__,
    Story.__table__,
    QuickAction.__table__,
    FeaturedContent.__table__,
    MemoryItem.__table__,
    MarketplaceCategory.__table__,
    Vendor.__table__,
    Product.__table__,
    NewsArticle.__table__,
    VendorVerification.__table__,
    SavedItem.__table__,
    SearchEvent.__table__,
    Review.__table__,
    Order.__table__,
    OrderItem.__table__,
    Transaction.__table__,
]


def get_url():
    """Get the database URL from environment."""
    return Config.DATABASE_URL


def run_migrations_offline():
    """Run migrations in offline mode."""
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online():
    """Run migrations in online mode."""
    connectable = engine_from_config(
        config.get_section(config.config_ini_section),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()