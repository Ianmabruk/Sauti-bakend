"""WSGI entry point for SautiPay Flask app."""
from backend.app import create_app
from backend.db import db
from backend.config import get_config

app = create_app(get_config())

# Create tables for development convenience
# In production, use Alembic migrations instead
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=8000, debug=False)