"""Initialize database tables for SautiPay."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.db import db
from backend.config import Config

def init_db():
    """Create all database tables."""
    app = create_app(Config)
    with app.app_context():
        db.create_all()
        print("Database tables created successfully.")

if __name__ == "__main__":
    init_db()
