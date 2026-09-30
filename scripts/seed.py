"""Seed script for SautiPay database.

Populates initial data: languages, intents, etc.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.db import db
from backend.models import Language, Intent
from backend.config import Config


def seed_database():
    """Seed the database with initial data."""
    app = create_app(Config)

    with app.app_context():
        # Create tables if they don't exist
        db.create_all()

        # Seed languages
        languages_data = [
            {"code": "en", "name": "English", "native_name": "English", "is_active": True, "has_verified_data": True},
            {"code": "sw", "name": "Kiswahili", "native_name": "Kiswahili", "is_active": True, "has_verified_data": True},
            {"code": "luo", "name": "Luo", "native_name": "Luo", "is_active": True, "has_verified_data": False},
            {"code": "kikuyu", "name": "Kikuyu", "native_name": "Gĩkũyũ", "is_active": True, "has_verified_data": False},
            {"code": "kamba", "name": "Kamba", "native_name": "Kamba", "is_active": True, "has_verified_data": False},
            {"code": "kisii", "name": "Kisii", "native_name": "Kisii", "is_active": True, "has_verified_data": False},
            {"code": "meru", "name": "Meru", "native_name": "Mĩrũ", "is_active": True, "has_verified_data": False},
            {"code": "samburu", "name": "Samburu", "native_name": "Samburu", "is_active": True, "has_verified_data": False},
        ]

        for lang_data in languages_data:
            existing = Language.query.filter_by(code=lang_data["code"]).first()
            if not existing:
                language = Language(**lang_data)
                db.session.add(language)

        # Seed intents
        intents_data = [
            {"name": "greeting", "description": "User greetings and introductions", "is_active": True},
            {"name": "general_question", "description": "General questions about various topics", "is_active": True},
            {"name": "news", "description": "Requests for current news and events", "is_active": True},
            {"name": "government_information", "description": "Questions about government services and information", "is_active": True},
            {"name": "civic_information", "description": "Questions about civic rights and responsibilities", "is_active": True},
            {"name": "agriculture", "description": "Questions about farming, crops, and agricultural practices", "is_active": True},
            {"name": "market_price", "description": "Requests for current market prices of commodities", "is_active": True},
            {"name": "product_search", "description": "Search for products and services", "is_active": True},
            {"name": "order", "description": "Placing orders for products or services", "is_active": True},
            {"name": "payment", "description": "Questions about payment methods and transactions", "is_active": True},
            {"name": "help", "description": "Requests for help and information about capabilities", "is_active": True},
            {"name": "unknown", "description": "Unclassified or unrecognized intents", "is_active": True},
        ]

        for intent_data in intents_data:
            existing = Intent.query.filter_by(name=intent_data["name"]).first()
            if not existing:
                intent = Intent(**intent_data)
                db.session.add(intent)

        db.session.commit()
        print("Database seeded successfully.")


if __name__ == "__main__":
    seed_database()
