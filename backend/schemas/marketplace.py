"""Marketplace request/response schemas."""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SearchRequest(BaseModel):
    query: str = Field(..., min_length=2, max_length=400)
    category: Optional[str] = Field(None, max_length=60)
    limit: int = Field(20, ge=1, le=50)
    user_id: Optional[str] = Field(None, max_length=64)
    record: bool = True

    @field_validator("query")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        text = value.strip()
        if not text:
            raise ValueError("query must not be blank")
        return text


class VendorCreateRequest(BaseModel):
    business_name: str = Field(..., min_length=2, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    category: Optional[str] = Field(None, max_length=120)
    subcategories: list[str] = Field(default_factory=list)
    location: Optional[str] = Field(None, max_length=255)
    latitude: Optional[float] = Field(None, ge=-90, le=90)
    longitude: Optional[float] = Field(None, ge=-180, le=180)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=255)
    website: Optional[str] = Field(None, max_length=500)
    service_areas: list[str] = Field(default_factory=list)
    delivery_available: bool = False
    accepts_mpesa: bool = True
    user_id: Optional[str] = Field(None, max_length=64)


class VendorUpdateRequest(BaseModel):
    business_name: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = Field(None, max_length=4000)
    category: Optional[str] = Field(None, max_length=120)
    location: Optional[str] = Field(None, max_length=255)
    phone: Optional[str] = Field(None, max_length=40)
    email: Optional[str] = Field(None, max_length=255)
    website: Optional[str] = Field(None, max_length=500)
    service_areas: list[str] = Field(default_factory=list)
    delivery_available: Optional[bool] = None
    accepts_mpesa: Optional[bool] = None


class ProductCreateRequest(BaseModel):
    vendor_id: str = Field(..., min_length=1, max_length=64)
    name: str = Field(..., min_length=2, max_length=300)
    description: Optional[str] = Field(None, max_length=4000)
    category: Optional[str] = Field(None, max_length=120)
    price_minor: Optional[int] = Field(None, ge=0)
    currency: str = Field("KES", max_length=8)
    availability: str = Field("IN_STOCK", max_length=24)
    attributes: dict = Field(default_factory=dict)
    specifications: dict = Field(default_factory=dict)
    tags: list[str] = Field(default_factory=list)


class NewsCreateRequest(BaseModel):
    title: str = Field(..., min_length=4, max_length=400)
    summary: Optional[str] = Field(None, max_length=2000)
    content: Optional[str] = Field(None, max_length=20000)
    category: Optional[str] = Field(None, max_length=120)
    source: Optional[str] = Field(None, max_length=255)
    source_url: Optional[str] = Field(None, max_length=1000)
    image: Optional[str] = Field(None, max_length=600)
    is_published: bool = False
    is_featured: bool = False


class NewsUpdateRequest(BaseModel):
    title: Optional[str] = Field(None, max_length=400)
    summary: Optional[str] = Field(None, max_length=2000)
    content: Optional[str] = Field(None, max_length=20000)
    category: Optional[str] = Field(None, max_length=120)
    source: Optional[str] = Field(None, max_length=255)
    source_url: Optional[str] = Field(None, max_length=1000)
    image: Optional[str] = Field(None, max_length=600)
    is_published: Optional[bool] = None
    is_featured: Optional[bool] = None
