"""Marketplace tests: query parsing, hybrid search, tools and the
eight acceptance scenarios from the product brief.
"""
from __future__ import annotations

import asyncio
import pytest

from backend.db import db
from backend.marketplace.models import (
    MarketplaceCategory,
    NewsArticle,
    Product,
    Vendor,
    VendorVerification,
)
from backend.marketplace.query import (
    is_discovery_request,
    is_news_request,
    parse_query,
)
from backend.marketplace.search import search_products
from backend.marketplace.service import NO_VENDOR_MESSAGE, get_marketplace_service
from backend.marketplace.tools import (
    GetProductDetailsTool,
    GetVendorProfileTool,
    SearchMarketplaceTool,
    SearchNewsTool,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture
def catalogue(app):
    """A small, explicit catalogue: every row created by this fixture."""
    with app.app_context():
        db.create_all()

        category = MarketplaceCategory(
            slug="vehicles", name="Vehicles", icon="car", accent="#8B5E3C", sort_order=1
        )
        agri = MarketplaceCategory(
            slug="agriculture", name="Agriculture", icon="sprout", accent="#4F7A3A", sort_order=2
        )
        db.session.add_all([category, agri])

        nairobi = Vendor(
            slug="autolux-motors",
            business_name="AutoLux Motors",
            category="vehicles",
            location="Nairobi",
            latitude=-1.2921,
            longitude=36.8219,
            verification_status="APPROVED",
            rating=4.8,
            phone="+254700000001",
            service_areas=["Nairobi", "Mombasa"],
            delivery_available=True,
        )
        eldoret = Vendor(
            slug="savannah-4x4",
            business_name="Savannah 4x4 Hub",
            category="vehicles",
            location="Eldoret",
            verification_status="APPROVED",
            rating=4.5,
            service_areas=["Eldoret", "Kakamega"],
        )
        unverified = Vendor(
            slug="grey-market-cars",
            business_name="Grey Market Cars",
            category="vehicles",
            location="Nairobi",
            verification_status="PENDING",
        )
        agro = Vendor(
            slug="meru-agro",
            business_name="Meru Agro Traders",
            category="agriculture",
            location="Meru",
            verification_status="APPROVED",
            service_areas=["Meru", "Embu"],
        )
        db.session.add_all([nairobi, eldoret, unverified, agro])
        db.session.commit()

        # The user's words are "G-Wagon 600 hp"; the listing says "G63 AMG 585 HP".
        db.session.add(Product(
            vendor_id=nairobi.id, name="Mercedes-Benz G63 AMG 2025",
            category="vehicles", price_minor=28_500_000, currency="KES",
            availability="IN_STOCK", location="Nairobi",
            attributes={"colour": "blue", "horsepower": 585},
            specifications={"engine": "4.0L V8", "horsepower": "585 HP"},
            tags=["mercedes", "g63", "g-wagon", "suv", "blue"],
        ))
        db.session.add(Product(
            vendor_id=nairobi.id, name="Mercedes-Benz GLE 450 4MATIC",
            category="vehicles", price_minor=24_900_000, currency="KES",
            availability="IN_STOCK", location="Nairobi",
            attributes={"colour": "black", "horsepower": 375},
            specifications={"horsepower": "375 HP"},
            tags=["mercedes", "gle", "suv"],
        ))
        db.session.add(Product(
            vendor_id=eldoret.id, name="Mercedes G-Wagon G500 2021",
            category="vehicles", price_minor=26_000_000, currency="KES",
            availability="IN_STOCK", location="Eldoret",
            attributes={"colour": "silver", "horsepower": 500},
            specifications={"horsepower": "500 HP"},
            tags=["mercedes", "g-wagon", "g500"],
        ))
        db.session.add(Product(
            vendor_id=unverified.id, name="G-Wagon grey import 2016",
            category="vehicles", price_minor=11_000_000, currency="KES",
            availability="IN_STOCK", location="Nairobi",
            attributes={"colour": "grey", "horsepower": 550},
            specifications={"horsepower": "550 HP"},
            tags=["g-wagon", "import"],
        ))
        db.session.add(Product(
            vendor_id=agro.id, name="Yanza beans (Rosecoco), 90kg bag",
            category="agriculture", price_minor=14_500, currency="KES",
            availability="IN_STOCK", location="Meru",
            attributes={"crop": "beans", "variety": "Yanza", "unit": "90kg bag"},
            specifications={"grade": "Grade 1"},
            tags=["beans", "maharagwe", "yanza"],
        ))
        news = NewsArticle(
            slug="kenya-grain-markets",
            title="Kenya grain markets report steady supply",
            summary="Wholesale grain prices held within a narrow band.",
            category="agriculture", source="Sauti Markets Desk",
            source_type="editorial", is_published=True,
        )
        db.session.add(news)
        db.session.commit()

        yield {
            "nairobi": nairobi.id,
            "eldoret": eldoret.id,
            "unverified": unverified.id,
            "agro": agro.id,
            "news": news.id,
        }

        db.session.rollback()


class TestQueryParsing:
    def test_parses_vehicle_request(self):
        parsed = parse_query("Find a blue Mercedes G-Wagon with around 600 horsepower in Nairobi.")
        assert parsed.category == "vehicles"
        assert parsed.vehicle_model == "g-wagon"
        assert parsed.color == "blue"
        assert parsed.horsepower == 600
        assert parsed.location == "Nairobi"

    def test_parses_swahili_request(self):
        parsed = parse_query("Natafuta mtu anauza maharagwe ya Yanza, mimi niko Samburu.")
        assert parsed.category == "agriculture"
        assert parsed.location == "Samburu"
        assert "maharagwe" in parsed.keywords

    def test_parses_french_request(self):
        parsed = parse_query("Quel est le prix actuel du maïs au Kenya ?")
        assert parsed.category == "agriculture"

    def test_does_not_invent_constraints(self):
        """Unstated constraints stay None rather than being defaulted."""
        parsed = parse_query("I want a car")
        assert parsed.color is None
        assert parsed.horsepower is None
        assert parsed.location is None
        assert parsed.max_price_minor is None

    def test_discovery_detection(self):
        assert is_discovery_request("I need a blue Mercedes G-Wagon in Nairobi") is True
        assert is_discovery_request("What is photosynthesis?") is False

    def test_news_detection(self):
        assert is_news_request("What are the latest news stories in Kenya?") is True
        assert is_news_request("Find me a tractor") is False


class TestHybridSearch:
    def test_g_wagon_request_matches_g63_listing(self, app, catalogue):
        with app.app_context():
            results, parsed, _ = search_products(
                "Find a blue Mercedes G-Wagon with around 600 horsepower in Nairobi."
            )
            assert results
            top = results[0].product
            assert "G63 AMG" in top.name
            assert top.vendor.business_name == "AutoLux Motors"

    def test_specific_model_outranks_generic(self, app, catalogue):
        with app.app_context():
            results, _, _ = search_products("How much is a Mercedes GLE in Nairobi?")
            assert "GLE" in results[0].product.name

    def test_swahili_agriculture_query(self, app, catalogue):
        with app.app_context():
            results, _, _ = search_products(
                "Natafuta mtu anauza maharagwe ya Yanza, mimi niko Samburu."
            )
            assert results
            assert "Yanza" in results[0].product.name

    def test_unrelated_query_returns_nothing(self, app, catalogue):
        with app.app_context():
            results, _, _ = search_products("quantum entanglement debugger submarine")
            assert results == []

    def test_score_components_are_reported(self, app, catalogue):
        with app.app_context():
            results, _, diagnostics = search_products("blue g-wagon nairobi")
            components = results[0].to_dict()["matchComponents"]
            assert set(components) == {"lexical", "fuzzy", "attributes", "context"}
            # Vector similarity is NOT active, and the diagnostics say so.
            assert diagnostics["vectorEnabled"] is False
            assert "vectorNote" in diagnostics

    def test_verified_only_filter(self, app, catalogue):
        """Asking for verified vendors must exclude unverified ones."""
        with app.app_context():
            default, _, _ = search_products("g-wagon nairobi", limit=30)
            statuses = {r.product.vendor.verification_status for r in default}
            # Unverified vendors are searchable by default, but ranked lower.
            assert "PENDING" in statuses

            verified, parsed, _ = search_products("verified g-wagon nairobi", limit=30)
            assert parsed.only_verified is True
            assert all(r.product.vendor.verification_status == "APPROVED" for r in verified)
            assert all(r.score > 0 for r in verified)

    def test_no_results_message(self, app, catalogue):
        with app.app_context():
            result = get_marketplace_service().search("submarine periscope", record=False)
            assert result["resultCount"] == 0
            assert result["message"] == NO_VENDOR_MESSAGE


class TestMarketplaceTools:
    def test_search_marketplace_tool(self, app, catalogue):
        with app.app_context():
            tool = SearchMarketplaceTool()
            result = run(tool.run({"query": "blue g-wagon nairobi"}))
            assert result.ok is True
            assert result.data["resultCount"] > 0
            assert result.data["results"][0]["vendor"]["businessName"]

    def test_empty_search_is_ok_not_error(self, app, catalogue):
        with app.app_context():
            tool = SearchMarketplaceTool()
            result = run(tool.run({"query": "submarine periscope"}))
            assert result.ok is True
            assert result.data["resultCount"] == 0
            assert result.data["message"] == NO_VENDOR_MESSAGE

    def test_vendor_profile_tool(self, app, catalogue):
        with app.app_context():
            tool = GetVendorProfileTool()
            result = run(tool.run({"vendor_id": "autolux-motors"}))
            assert result.ok is True
            assert result.data["found"] is True
            assert result.data["vendor"]["businessName"] == "AutoLux Motors"

    def test_vendor_not_found_is_explicit(self, app, catalogue):
        with app.app_context():
            tool = GetVendorProfileTool()
            result = run(tool.run({"vendor_id": "nope"}))
            assert result.ok is True
            assert result.data["found"] is False

    def test_product_details_tool(self, app, catalogue):
        with app.app_context():
            product = Product.query.filter_by(name="Mercedes-Benz G63 AMG 2025").first()
            tool = GetProductDetailsTool()
            result = run(tool.run({"product_id": product.id}))
            assert result.data["found"] is True
            assert result.data["product"]["price"] == "KSh 28,500,000"

    def test_news_tool_accepts_full_sentence(self, app, catalogue):
        with app.app_context():
            tool = SearchNewsTool()
            result = run(tool.run({"query": "What are the latest major news stories in Kenya?"}))
            assert result.ok is True
            assert result.data["count"] >= 1


class TestAcceptanceScenarios:
    """The eight scenarios from the product brief."""

    def test_1_vehicle_search_finds_real_vendors(self, app, catalogue):
        with app.app_context():
            result = get_marketplace_service().search(
                "Find a blue Mercedes G-Wagon with around 600 horsepower in Nairobi.",
                record=False,
            )
            assert result["resultCount"] > 0
            top = result["results"][0]
            assert "Mercedes" in top["name"]
            assert top["vendor"]["businessName"]
            assert top["price"]

    def test_2_swahili_search(self, app, catalogue):
        with app.app_context():
            result = get_marketplace_service().search(
                "Natafuta mtu anauza maharagwe ya Yanza, mimi niko Samburu.", record=False
            )
            assert result["resultCount"] > 0
            assert "Yanza" in result["results"][0]["name"]

    def test_3_news_comes_from_own_database(self, app, catalogue):
        with app.app_context():
            tool = SearchNewsTool()
            result = run(tool.run({"query": "latest news in Kenya"}))
            assert result.ok is True
            articles = result.data["articles"]
            assert articles
            for article in articles:
                assert article["title"]
                assert article["id"]

    def test_4_never_invents_a_price(self, app, catalogue):
        with app.app_context():
            tool = SearchMarketplaceTool()
            empty = run(tool.run({"query": "quantum debugger"}))
            assert empty.data["resultCount"] == 0
            # The only price that may appear came from a real row.
            found = run(tool.run({"query": "blue g-wagon nairobi"}))
            for item in found.data["results"]:
                assert item["price"] is None or item["price"].startswith("KSh")

    def test_6_new_vendor_is_searchable_without_retraining(self, app, catalogue):
        with app.app_context():
            vendor = Vendor(
                slug="nyota-motors", business_name="Nyota Motors",
                category="vehicles", location="Nairobi",
                verification_status="APPROVED",
            )
            db.session.add(vendor)
            db.session.commit()
            db.session.add(Product(
                vendor_id=vendor.id, name="Nyota G-Wagon 620 HP blue",
                category="vehicles", price_minor=12_000_000, currency="KES",
                availability="IN_STOCK", location="Nairobi",
                attributes={"colour": "blue", "horsepower": 620},
                specifications={"horsepower": "620 HP"},
                tags=["g-wagon", "nyota", "blue"],
            ))
            db.session.commit()

            result = get_marketplace_service().search("nyota g-wagon blue nairobi", record=False)
            assert result["resultCount"] > 0
            hit = [r for r in result["results"] if "Nyota" in r["name"]]
            assert hit
            assert hit[0]["price"] == "KSh 12,000,000"

    def test_7_price_change_visible_immediately(self, app, catalogue):
        with app.app_context():
            product = Product.query.filter_by(name="Mercedes-Benz G63 AMG 2025").first()
            product.price_minor = 31_750_000
            db.session.commit()

            result = get_marketplace_service().search("blue g-wagon nairobi", record=False)
            hit = [r for r in result["results"] if "G63" in r["name"]]
            assert hit
            assert hit[0]["price"] == "KSh 31,750,000"

    def test_8_no_match_never_invents_a_seller(self, app, catalogue):
        with app.app_context():
            result = get_marketplace_service().search("submarine periscope", record=False)
            assert result["results"] == []
            assert result["message"] == NO_VENDOR_MESSAGE


class TestDataIntegrity:
    def test_results_trace_to_real_vendors(self, app, catalogue):
        """Every result must reference a vendor that exists in the database."""
        with app.app_context():
            result = get_marketplace_service().search("g-wagon nairobi", record=False)
            for item in result["results"]:
                assert db.session.get(Vendor, item["vendorId"]) is not None
                assert db.session.get(Product, item["id"]) is not None

    def test_vendor_serves(self, app, catalogue):
        with app.app_context():
            nairobi = db.session.get(Vendor, catalogue["nairobi"])
            assert nairobi.serves("Nairobi") is True
            assert nairobi.serves("Eldoret") is False
            # No service areas stated: unknown, not a silent False.
            agro = db.session.get(Vendor, catalogue["agro"])
            assert agro.serves("Kisumu") is False  # areas are stated
            unverified = db.session.get(Vendor, catalogue["unverified"])
            assert unverified.serves("Kisumu") is None

    def test_price_formats_without_floating_point(self, app, catalogue):
        with app.app_context():
            product = Product.query.filter_by(name="Yanza beans (Rosecoco), 90kg bag").first()
            assert product.price_minor == 14_500
            assert product.display_price() == "KSh 14,500"
