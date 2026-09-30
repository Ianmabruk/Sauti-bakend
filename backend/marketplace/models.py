"""Sauti marketplace data model.

Design rules:
- The database is the source of truth for vendors and products. The language
  model never supplies inventory facts; it only requests a search.
- `embedding` is stored as JSON so the schema works identically on SQLite and
  PostgreSQL. On PostgreSQL this can be moved to a pgvector column with an
  Alembic migration without changing application code (see VectorIndex).
- Money is stored in minor units (integer) to avoid float drift.
- Nothing here is destructive: these are new tables only.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from ..db import db
from ..models import generate_uuid, TimestampMixin

#: Verification states a vendor can hold. Only APPROVED vendors are shown as
#: "Verified" to the public.
VERIFICATION_STATES = ("PENDING", "IN_REVIEW", "APPROVED", "REJECTED", "SUSPENDED")

AVAILABILITY_STATES = ("IN_STOCK", "LOW_STOCK", "OUT_OF_STOCK", "PRE_ORDER", "ON_ORDER")

#: Categories that exist in the platform. Extensible via the category table.
DEFAULT_CATEGORIES = (
    ("vehicles", "Vehicles", "car", "#8B5E3C"),
    ("agriculture", "Agriculture", "sprout", "#4F7A3A"),
    ("real_estate", "Real Estate", "home", "#A8763E"),
    ("jobs", "Jobs", "briefcase", "#6B5B4A"),
    ("services", "Services", "wrench", "#8B5E3C"),
    ("electronics", "Electronics", "device", "#3F5A6B"),
    ("fashion", "Fashion", "shirt", "#9C6B7A"),
    ("food", "Food", "basket", "#B06A3B"),
    ("travel", "Travel", "plane", "#4A6B7C"),
    ("health", "Health", "heart", "#7C5B6B"),
    ("education", "Education", "book", "#6B5B3A"),
    ("construction", "Construction", "tool", "#7A6248"),
)


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _slugify(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return slug or generate_uuid()[:8]


class MarketplaceCategory(TimestampMixin, db.Model):
    """A discovery category (Vehicles, Agriculture, …)."""

    __tablename__ = "marketplace_categories"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    slug = db.Column(db.String(120), unique=True, nullable=False)
    name = db.Column(db.String(120), nullable=False)
    description = db.Column(db.Text, nullable=True)
    icon = db.Column(db.String(60), nullable=True)
    accent = db.Column(db.String(24), default="#8B5E3C", nullable=False)
    image_url = db.Column(db.String(600), nullable=True)
    sort_order = db.Column(db.Integer, default=0, nullable=False)
    is_active = db.Column(db.Boolean, default=True, nullable=False)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "slug": self.slug,
            "name": self.name,
            "description": self.description,
            "icon": self.icon,
            "accent": self.accent,
            "image": self.image_url,
            "sortOrder": self.sort_order,
        }


class Vendor(TimestampMixin, db.Model):
    """A business listing its inventory on Sauti."""

    __tablename__ = "vendors"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    business_name = db.Column(db.String(255), nullable=False)
    slug = db.Column(db.String(255), unique=True, nullable=False)
    description = db.Column(db.Text, nullable=True)

    category = db.Column(db.String(120), nullable=True)
    subcategories = db.Column(db.JSON, nullable=True)

    location = db.Column(db.String(255), nullable=True)
    latitude = db.Column(db.Float, nullable=True)
    longitude = db.Column(db.Float, nullable=True)

    phone = db.Column(db.String(40), nullable=True)
    email = db.Column(db.String(255), nullable=True)
    website = db.Column(db.String(500), nullable=True)

    logo_url = db.Column(db.String(600), nullable=True)
    cover_url = db.Column(db.String(600), nullable=True)

    verification_status = db.Column(
        db.String(20), default="PENDING", nullable=False
    )
    rating = db.Column(db.Float, default=None, nullable=True)
    review_count = db.Column(db.Integer, default=0, nullable=False)

    opening_hours = db.Column(db.JSON, nullable=True)
    service_areas = db.Column(db.JSON, nullable=True)
    delivery_available = db.Column(db.Boolean, default=False, nullable=False)
    accepts_mpesa = db.Column(db.Boolean, default=True, nullable=False)

    owner_user_id = db.Column(
        db.String, db.ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    products = db.relationship(
        "Product", back_populates="vendor", cascade="all, delete-orphan"
    )

    __table_args__ = (
        db.Index("idx_vendors_category", "category"),
        db.Index("idx_vendors_location", "location"),
        db.Index("idx_vendors_verification", "verification_status"),
    )

    @property
    def is_verified(self) -> bool:
        return self.verification_status == "APPROVED"

    @property
    def service_area_list(self) -> list:
        if not self.service_areas:
            return []
        return [str(area) for area in self.service_areas if str(area).strip()]

    def serves(self, place: str) -> bool | None:
        """Whether this vendor serves `place`. None means "not stated"."""
        if not place:
            return None
        areas = self.service_area_list
        if not areas:
            return None
        target = place.strip().lower()
        for area in areas:
            if target in area.lower() or area.lower() in target:
                return True
        return False

    def to_dict(self, *, include_contact: bool = True) -> dict:
        return {
            "id": self.id,
            "businessName": self.business_name,
            "slug": self.slug,
            "description": self.description,
            "category": self.category,
            "subcategories": self.subcategories or [],
            "location": self.location,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "phone": self.phone if include_contact else None,
            "email": self.email if include_contact else None,
            "website": self.website,
            "logo": self.logo_url,
            "cover": self.cover_url,
            "verificationStatus": self.verification_status,
            "isVerified": self.is_verified,
            "rating": self.rating,
            "reviewCount": self.review_count,
            "openingHours": self.opening_hours or {},
            "serviceAreas": self.service_area_list,
            "deliveryAvailable": self.delivery_available,
            "acceptsMpesa": self.accepts_mpesa,
        }


class Product(TimestampMixin, db.Model):
    """A single product or service offered by a vendor."""

    __tablename__ = "products"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    vendor_id = db.Column(
        db.String, db.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )

    name = db.Column(db.String(300), nullable=False)
    slug = db.Column(db.String(320), nullable=True)
    description = db.Column(db.Text, nullable=True)

    category = db.Column(db.String(120), nullable=True)
    subcategory = db.Column(db.String(120), nullable=True)

    #: Minor units (e.g. cents, or Kenyan shillings) to avoid float drift.
    price_minor = db.Column(db.Integer, nullable=True)
    currency = db.Column(db.String(8), default="KES", nullable=False)
    price_label = db.Column(db.String(120), nullable=True)

    availability = db.Column(db.String(24), default="IN_STOCK", nullable=False)
    quantity_available = db.Column(db.Integer, nullable=True)
    location = db.Column(db.String(255), nullable=True)

    images = db.Column(db.JSON, nullable=True)
    attributes = db.Column(db.JSON, nullable=True)
    specifications = db.Column(db.JSON, nullable=True)
    tags = db.Column(db.JSON, nullable=True)

    #: Search vector, stored as JSON for SQLite/Postgres portability.
    embedding = db.Column(db.JSON, nullable=True)
    embedding_model = db.Column(db.String(80), nullable=True)
    search_text = db.Column(db.Text, nullable=True)

    is_active = db.Column(db.Boolean, default=True, nullable=False)
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    view_count = db.Column(db.Integer, default=0, nullable=False)
    inquiry_count = db.Column(db.Integer, default=0, nullable=False)

    vendor = db.relationship("Vendor", back_populates="products")

    __table_args__ = (
        db.Index("idx_products_vendor", "vendor_id"),
        db.Index("idx_products_category", "category"),
        db.Index("idx_products_availability", "availability"),
        db.Index("idx_products_price", "price_minor"),
        db.Index("idx_products_active", "is_active"),
    )

    @property
    def is_available(self) -> bool:
        return self.availability in {"IN_STOCK", "LOW_STOCK", "PRE_ORDER", "ON_ORDER"}

    @property
    def image_list(self) -> list:
        return [str(i) for i in (self.images or []) if str(i).strip()]

    def display_price(self) -> str | None:
        if self.price_label:
            return self.price_label
        if self.price_minor is None:
            return None
        amount = self.price_minor
        if self.currency == "KES" and amount >= 1000:
            return f"KSh {amount:,}"
        return f"{self.currency} {amount:,}"

    def spec_pairs(self) -> list:
        """Specifications as ordered label/value pairs for rendering."""
        specs = self.specifications or {}
        return [
            {"label": str(k), "value": str(v)}
            for k, v in specs.items()
            if v not in (None, "", [])
        ]

    def attribute_pairs(self) -> list:
        attributes = self.attributes or {}
        return [
            {"label": str(k).replace("_", " ").title(), "value": str(v)}
            for k, v in attributes.items()
            if v not in (None, "", [])
        ]

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "vendorId": self.vendor_id,
            "vendorName": self.vendor.business_name if self.vendor else None,
            "isVendorVerified": self.vendor.is_verified if self.vendor else False,
            "name": self.name,
            "description": self.description,
            "category": self.category,
            "subcategory": self.subcategory,
            "price": self.display_price(),
            "priceMinor": self.price_minor,
            "currency": self.currency,
            "availability": self.availability,
            "isAvailable": self.is_available,
            "quantityAvailable": self.quantity_available,
            "location": self.location,
            "images": self.image_list,
            "attributes": self.attribute_pairs(),
            "specifications": self.spec_pairs(),
            "tags": [str(t) for t in (self.tags or [])],
            "isActive": self.is_active,
            "isFeatured": self.is_featured,
            "viewCount": self.view_count,
            "createdAt": self.created_at.isoformat() if self.created_at else None,
            "updatedAt": self.updated_at.isoformat() if self.updated_at else None,
        }


class NewsArticle(TimestampMixin, db.Model):
    """Editorial news shown on the homepage. Managed by admins."""

    __tablename__ = "news_articles"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    title = db.Column(db.String(400), nullable=False)
    slug = db.Column(db.String(420), unique=True, nullable=False)
    summary = db.Column(db.Text, nullable=True)
    content = db.Column(db.Text, nullable=True)

    image_url = db.Column(db.String(600), nullable=True)
    category = db.Column(db.String(120), nullable=True)
    source = db.Column(db.String(255), nullable=True)
    source_url = db.Column(db.String(1000), nullable=True)
    source_type = db.Column(db.String(40), default="editorial", nullable=False)

    published_at = db.Column(db.DateTime(timezone=True), nullable=True)
    retrieved_at = db.Column(db.DateTime(timezone=True), nullable=True)
    is_published = db.Column(db.Boolean, default=False, nullable=False)
    is_featured = db.Column(db.Boolean, default=False, nullable=False)
    sort_order = db.Column(db.Integer, default=0, nullable=False)

    __table_args__ = (
        db.Index("idx_news_published", "is_published"),
        db.Index("idx_news_category", "category"),
    )

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "title": self.title,
            "slug": self.slug,
            "summary": self.summary,
            "image": self.image_url,
            "category": self.category,
            "source": self.source,
            "sourceUrl": self.source_url,
            "sourceType": self.source_type,
            "publishedAt": self.published_at.isoformat() if self.published_at else None,
            "isFeatured": self.is_featured,
        }


class VendorVerification(TimestampMixin, db.Model):
    """Audit trail of vendor verification decisions."""

    __tablename__ = "vendor_verifications"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    vendor_id = db.Column(
        db.String, db.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    status = db.Column(db.String(20), nullable=False)
    note = db.Column(db.Text, nullable=True)
    document_reference = db.Column(db.String(255), nullable=True)
    reviewed_by = db.Column(db.String(64), nullable=True)

    vendor = db.relationship("Vendor")


class SavedItem(TimestampMixin, db.Model):
    """A user bookmarking a vendor or product."""

    __tablename__ = "saved_items"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    user_id = db.Column(db.String, nullable=True)
    item_type = db.Column(db.String(24), nullable=False)
    item_id = db.Column(db.String, nullable=False)

    __table_args__ = (
        db.Index("idx_saved_user", "user_id"),
        db.Index("idx_saved_item", "item_type", "item_id"),
    )


class SearchEvent(TimestampMixin, db.Model):
    """Search analytics. Powers Popular Searches from real data only."""

    __tablename__ = "search_events"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    # Named query_text, not query: a column called `query` shadows Model.query.
    query_text = db.Column(db.Text, nullable=False)
    normalised_query = db.Column(db.String(400), nullable=True)
    category = db.Column(db.String(120), nullable=True)
    location = db.Column(db.String(255), nullable=True)
    language = db.Column(db.String(10), nullable=True)
    result_count = db.Column(db.Integer, default=0, nullable=False)
    had_results = db.Column(db.Boolean, default=True, nullable=False)
    user_id = db.Column(db.String, nullable=True)
    source = db.Column(db.String(40), default="search_bar", nullable=False)
    duration_ms = db.Column(db.Integer, default=0, nullable=False)


class Review(TimestampMixin, db.Model):
    """A rating left against a vendor."""

    __tablename__ = "reviews"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    vendor_id = db.Column(
        db.String, db.ForeignKey("vendors.id", ondelete="CASCADE"), nullable=False
    )
    user_id = db.Column(db.String, nullable=True)
    rating = db.Column(db.Integer, nullable=False)
    body = db.Column(db.Text, nullable=True)
    is_published = db.Column(db.Boolean, default=True, nullable=False)

    vendor = db.relationship("Vendor")


class Order(TimestampMixin, db.Model):
    """A user order. Created only by an explicit, backend-validated action."""

    __tablename__ = "orders"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    reference = db.Column(db.String(40), unique=True, nullable=False)
    user_id = db.Column(db.String, nullable=True)
    vendor_id = db.Column(db.String, db.ForeignKey("vendors.id", ondelete="SET NULL"), nullable=True)
    status = db.Column(db.String(24), default="PENDING", nullable=False)
    total_minor = db.Column(db.Integer, default=0, nullable=False)
    currency = db.Column(db.String(8), default="KES", nullable=False)
    delivery_details = db.Column(db.JSON, nullable=True)
    created_by = db.Column(db.String(32), default="user", nullable=False)


class OrderItem(TimestampMixin, db.Model):
    __tablename__ = "order_items"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    order_id = db.Column(
        db.String, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    product_id = db.Column(
        db.String, db.ForeignKey("products.id", ondelete="SET NULL"), nullable=True
    )
    product_name = db.Column(db.String(300), nullable=False)
    unit_price_minor = db.Column(db.Integer, nullable=False)
    quantity = db.Column(db.Integer, default=1, nullable=False)


class Transaction(TimestampMixin, db.Model):
    """Payment attempts. A transaction is never 'successful' until the
    provider callback confirms it."""

    __tablename__ = "transactions"

    id = db.Column(db.String, primary_key=True, default=generate_uuid)
    order_id = db.Column(
        db.String, db.ForeignKey("orders.id", ondelete="CASCADE"), nullable=False
    )
    provider = db.Column(db.String(40), default="mpesa", nullable=False)
    provider_reference = db.Column(db.String(120), nullable=True)
    amount_minor = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(8), default="KES", nullable=False)
    phone = db.Column(db.String(40), nullable=True)
    status = db.Column(db.String(24), default="INITIATED", nullable=False)
    raw_response = db.Column(db.JSON, nullable=True)
