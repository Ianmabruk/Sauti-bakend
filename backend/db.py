"""Database setup for SautiPay."""
from flask import Flask
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate

db = SQLAlchemy()
migrate = Migrate()


def init_db(app: Flask) -> None:
    """Initialize database extensions."""
    db.init_app(app)
    migrate.init_app(app, db)