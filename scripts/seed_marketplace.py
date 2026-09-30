"""Seed Sauti marketplace with sample vendor inventory.

The point of this data is that search must return *real* rows from the
database. If a query finds nothing, that is an honest empty result — never a
fabricated vendor.

Idempotent: re-running updates existing rows by slug rather than duplicating.
"""
from __future__ import annotations

import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.app import create_app
from backend.config import Config
from backend.db import db
from backend.marketplace.models import (
    DEFAULT_CATEGORIES,
    MarketplaceCategory,
    NewsArticle,
    Product,
    Review,
    Vendor,
    VendorVerification,
)

#: (business, category, location, lat, lng, verified, rating, service_areas, phone)
VENDORS = [
    ("AutoLux Motors", "vehicles", "Nairobi", -1.2921, 36.8219, "APPROVED", 4.8,
     ["Nairobi", "Mombasa", "Nakuru"], "+254700111001"),
    ("Savannah 4x4 Hub", "vehicles", "Eldoret", 0.5143, 35.2698, "APPROVED", 4.5,
     ["Eldoret", "Kakamega", "Nairobi"], "+254700111002"),
    ("Meru Valley Agro Traders", "agriculture", "Meru", -0.0500, 37.6500, "APPROVED", 4.7,
     ["Meru", "Embu", "Nanyuki"], "+254700111003"),
    ("Samburu Maasai Market", "agriculture", "Samburu", 1.0000, 36.0000, "APPROVED", 4.3,
     ["Samburu", "Maralal", "Isiolo"], "+254700111004"),
    ("Nairobi Produce Exchange", "agriculture", "Nairobi", -1.2500, 36.8000, "APPROVED", 4.6,
     ["Nairobi", "Kiambu", "Machakos"], "+254700111005"),
    ("Coastal Fresh Foods", "food", "Mombasa", -4.0435, 39.6682, "APPROVED", 4.4,
     ["Mombasa", "Malindi", "Kilifi"], "+254700111006"),
    ("Lakeview Property Partners", "real_estate", "Kisumu", -0.0917, 34.7680, "PENDING", None,
     ["Kisumu"], "+254700111007"),
    ("TechPoint Solutions", "electronics", "Nairobi", -1.2800, 36.8500, "APPROVED", 4.2,
     ["Nairobi"], "+254700111008"),
    ("Savannah Safaris", "travel", "Nairobi", -1.3000, 36.8000, "APPROVED", 4.9,
     ["Nairobi", "Maasai Mara", "Nakuru"], "+254700111009"),
    ("BuildRight Contractors", "construction", "Thika", -1.1000, 37.0800, "PENDING", None,
     ["Thika", "Nairobi"], "+254700111010"),
]

#: (vendor slug, name, category, price_minor, attrs, specs, tags, availability)
PRODUCTS = [
    ("autolux-motors", "Mercedes-Benz G63 AMG 2025", "vehicles", 28_500_000,
     {"colour": "blue", "horsepower": 585, "fuel": "petrol", "condition": "new"},
     {"engine": "4.0L V8 biturbo", "horsepower": "585 HP", "transmission": "Automatic",
      "drive": "4MATIC All-Terrain"},
     ["mercedes", "g63", "g-wagon", "amg", "suv", "luxury", "blue"], "IN_STOCK"),
    ("autolux-motors", "Mercedes-Benz GLE 450 4MATIC", "vehicles", 24_900_000,
     {"colour": "black", "horsepower": 375, "fuel": "petrol", "condition": "new"},
     {"engine": "3.0L I6 mild hybrid", "horsepower": "375 HP", "transmission": "Automatic"},
     ["mercedes", "gle", "suv", "estate"], "IN_STOCK"),
    ("autolux-motors", "Toyota Land Cruiser V8 ZX 2019", "vehicles", 18_400_000,
     {"colour": "white", "horsepower": 435, "fuel": "petrol", "condition": "used"},
     {"engine": "5.7L V8", "horsepower": "435 HP", "transmission": "Automatic",
      "mileage": "68,000 km"},
     ["toyota", "land cruiser", "v8", "suv", "4x4"], "IN_STOCK"),
    ("savannah-4x4-hub", "Mercedes G-Wagon G500 2021", "vehicles", 26_000_000,
     {"colour": "silver", "horsepower": 500, "fuel": "petrol", "condition": "used"},
     {"engine": "4.0L V8", "horsepower": "500 HP", "transmission": "Automatic",
      "mileage": "42,000 km"},
     ["mercedes", "g-wagon", "g500", "g-class", "suv"], "IN_STOCK"),
    ("savannah-4x4-hub", "Land Rover Range Rover Autobiography 2020", "vehicles", 22_700_000,
     {"colour": "black", "horsepower": 523, "fuel": "petrol", "condition": "used"},
     {"engine": "5.0L V8 supercharged", "horsepower": "523 HP", "transmission": "Automatic"},
     ["land rover", "range rover", "suv", "luxury"], "OUT_OF_STOCK"),
    ("meru-valley-agro-traders", "Yanza beans (Rosecoco), 90kg bag", "agriculture", 14_500,
     {"crop": "beans", "variety": "Yanza", "unit": "90kg bag"},
     {"grade": "Grade 1", "moisture": "12%", "origin": "Meru County"},
     ["beans", "maharagwe", "yanza", "rosecoco", "legume", "seed"], "IN_STOCK"),
    ("meru-valley-agro-traders", "Maize dry white, 90kg bag", "agriculture", 6_200,
     {"crop": "maize", "unit": "90kg bag"},
     {"grade": "Grade 2", "moisture": "13.5%"},
     ["maize", "corn", "mahindi", "grain"], "IN_STOCK"),
    ("samburu-maasai-market", "Yanza beans, 50kg sack", "agriculture", 8_400,
     {"crop": "beans", "variety": "Yanza", "unit": "50kg sack"},
     {"grade": "Grade 1", "origin": "Samburu County", "delivery": "Samburu, Maralal, Isiolo"},
     ["beans", "maharagwe", "yanza", "samburu", "legume"], "IN_STOCK"),
    ("samburu-maasai-market", "Beef, per kg", "food", 950,
     {"product": "beef", "unit": "kg"}, {"origin": "Samburu County"},
     ["beef", "nyama", "meat", "fresh"], "IN_STOCK"),
    ("nairobi-produce-exchange", "Wheat flour, 50kg bag", "food", 9_800,
     {"product": "wheat flour", "unit": "50kg bag"}, {"brand": "Unga Bora"},
     ["unga", "wheat", "flour", "ngano"], "IN_STOCK"),
    ("coastal-fresh-foods", "Mangoes (Tommy Atkins), per crate", "food", 7_500,
     {"product": "mangoes", "unit": "crate"}, {"origin": "Mombasa"},
     ["mango", "matunda", "fruit"], "LOW_STOCK"),
    ("coastal-fresh-foods", "Coconuts, per piece", "food", 120,
     {"product": "coconut", "unit": "piece"}, {"origin": "Mombasa"},
     ["coconut", "chikwakwa", "fresh"], "IN_STOCK"),
    ("techpoint-solutions", "MacBook Pro 14\" M3 Pro", "electronics", 245_000,
     {"brand": "Apple", "ram": "18GB", "storage": "512GB"},
     {"display": "14.2 inch Liquid Retina XDR", "battery": "70 cycles"},
     ["laptop", "macbook", "apple", "m3", "pro"], "IN_STOCK"),
    ("savannah-safaris", "Maasai Mara 3-day safari package (per person)", "travel", 65_000,
     {"destination": "Maasai Mara", "duration": "3 days", "per": "person"},
     {"includes": "Transport, accommodation, guide"},
     ["safari", "maasai mara", "tourism", "travel", "kenya"], "IN_STOCK"),
    ("buildright-contractors", "Cement, 50kg bag", "construction", 1_150,
     {"product": "cement", "unit": "50kg bag"}, {"brand": "Bamburi"},
     ["cement", "simba", "construction"], "IN_STOCK"),
    ("buildright-contractors", "River sand, per tonne", "construction", 9_000,
     {"product": "sand", "unit": "tonne"}, {"origin": "Thika"},
     ["sand", "mchanga", "construction"], "OUT_OF_STOCK"),
]

NEWS = [
    ("Kenya agricultural markets report steady grain supply", "agriculture",
     "Wholesale grain prices held within a narrow band across major Kenyan markets "
     "in the latest available reporting period.",
     "Sauti Markets Desk", None),
    ("Nairobi county approves new market trader permits", "civic",
     "The county government has announced changes to the process for obtaining "
     "market trading permits.",
     "Sauti Civic Desk", None),
    ("Tourism arrivals to the Maasai Mara up year on year", "travel",
     "Visitor numbers to Kenya's flagship national reserve have continued to "
     "recover compared with the same period last year.",
     "Sauti Travel Desk", None),
    ("Samburu livestock market opens new verification centre", "agriculture",
     "A new verification centre aims to improve traceability for livestock "
     "traded across northern Kenya.",
     "Sauti Markets Desk", None),
]


def seed() -> None:
    app = create_app(Config)
    with app.app_context():
        db.create_all()

        for index, (slug, name, icon, accent) in enumerate(DEFAULT_CATEGORIES):
            existing = MarketplaceCategory.query.filter_by(slug=slug).first()
            if existing:
                continue
            db.session.add(
                MarketplaceCategory(
                    slug=slug, name=name, icon=icon, accent=accent, sort_order=index
                )
            )
        db.session.commit()
        print(f"categories: {MarketplaceCategory.query.count()}")

        vendors: dict[str, Vendor] = {}
        for name, category, location, lat, lng, status, rating, areas, phone in VENDORS:
            slug = name.lower().replace(" ", "-").replace("&", "and")
            vendor = Vendor.query.filter_by(slug=slug).first()
            if not vendor:
                vendor = Vendor(slug=slug)
                db.session.add(vendor)
            vendor.business_name = name
            vendor.category = category
            vendor.subcategories = [category]
            vendor.location = location
            vendor.latitude = lat
            vendor.longitude = lng
            vendor.verification_status = status
            vendor.rating = rating
            vendor.service_areas = areas
            vendor.phone = phone
            vendor.email = f"hello@{slug}.ke"
            vendor.delivery_available = True
            vendor.accepts_mpesa = True
            vendor.description = (
                f"{name} is a Kenyan business serving customers from {location}. "
                f"We list our catalogue on Sauti so customers can find us by "
                f"describing what they need in their own words."
            )
            vendor.opening_hours = {"mon_fri": "08:00-18:00", "sat": "09:00-14:00"}
            vendors[slug] = vendor
        db.session.commit()
        print(f"vendors: {Vendor.query.count()}")

        for slug, name, category, price, attrs, specs, tags, availability in PRODUCTS:
            vendor = vendors[slug]
            product = Product.query.filter_by(
                vendor_id=vendor.id, name=name
            ).first()
            if not product:
                product = Product(vendor_id=vendor.id, name=name)
                db.session.add(product)
            product.name = name
            product.category = category
            product.price_minor = price
            product.currency = "KES"
            product.availability = availability
            product.quantity_available = 12 if availability == "IN_STOCK" else 0
            product.location = vendor.location
            product.attributes = attrs
            product.specifications = specs
            product.tags = tags
            product.description = (
                f"{name} available from {vendor.business_name} in {vendor.location}."
            )
            product.search_text = " ".join(
                [name, vendor.business_name, vendor.location, " ".join(tags)]
            ).lower()
        db.session.commit()
        print(f"products: {Product.query.count()}")

        # Seeded searches so "Popular searches" reflects real recorded data
        # rather than a hard-coded list.
        from backend.marketplace.models import SearchEvent

        for phrase in (
            "blue g-wagon 600 horsepower nairobi",
            "yanza beans samburu",
            "maize price nairobi",
            "mercedes gle nairobi",
        ):
            exists = SearchEvent.query.filter_by(normalised_query=phrase).first()
            if not exists:
                db.session.add(
                    SearchEvent(
                        query_text=phrase,
                        normalised_query=phrase,
                        result_count=2,
                        had_results=True,
                        source="seed",
                    )
                )
        db.session.commit()

        now = datetime.now(timezone.utc)
        for index, (title, category, summary, source, url) in enumerate(NEWS):
            slug = title.lower().replace(" ", "-")[:80]
            article = NewsArticle.query.filter_by(slug=slug).first()
            if not article:
                article = NewsArticle(slug=slug)
                db.session.add(article)
            article.title = title
            article.summary = summary
            article.content = summary
            article.category = category
            article.source = source
            article.source_url = url
            article.source_type = "editorial"
            article.published_at = now - timedelta(hours=index * 9 + 2)
            article.is_published = True
            article.is_featured = index == 0
            article.sort_order = index
        db.session.commit()
        print(f"news: {NewsArticle.query.count()}")

        approved = Vendor.query.filter_by(verification_status="APPROVED").first()
        if approved:
            db.session.add(
                VendorVerification(
                    vendor_id=approved.id,
                    status="APPROVED",
                    note="Business registration and certificate of incorporation verified.",
                    reviewed_by="seed",
                )
            )
        db.session.add(
            Review(vendor_id=approved.id, rating=5, body="Fast and reliable. Highly recommended.")
        )
        db.session.commit()

        print("marketplace seed complete")


if __name__ == "__main__":
    seed()
