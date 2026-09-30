"""Tests for the frontend CMS content API."""
import base64
import io

import pytest

from backend.db import db
from backend.models import Category, HeroSlide, MediaAsset, Story


PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture
def admin_headers(app):
    app.config["ADMIN_TOKEN"] = "test-token"
    return {"X-Admin-Token": "test-token"}


def png_upload(name="hero.png"):
    return {"file": (io.BytesIO(PNG_BYTES), name)}


class TestContentAuthorization:
    """Only authorized admins may modify content."""

    def test_public_content_is_open(self, client):
        assert client.get("/api/content").status_code == 200

    def test_list_requires_token(self, client):
        assert client.get("/api/admin/content/category").status_code == 401

    def test_wrong_token_forbidden(self, client, admin_headers):
        assert client.get(
            "/api/admin/content/category", headers={"X-Admin-Token": "wrong"}
        ).status_code == 403

    def test_valid_token_allowed(self, client, admin_headers):
        assert client.get("/api/admin/content/category", headers=admin_headers).status_code == 200

    def test_bearer_token_allowed(self, client, admin_headers):
        assert client.get(
            "/api/admin/content/category", headers={"Authorization": "Bearer test-token"}
        ).status_code == 200

    def test_create_requires_token(self, client):
        assert client.post("/api/admin/content/category", json={"title": "News"}).status_code == 401


class TestCategoryManager:
    def test_create_and_list(self, client, admin_headers):
        response = client.post(
            "/api/admin/content/category",
            json={"title": "Agriculture", "isPublished": True},
            headers=admin_headers,
        )
        assert response.status_code == 201
        listed = client.get("/api/admin/content/category", headers=admin_headers).get_json()
        assert [i["title"] for i in listed["items"]] == ["Agriculture"]

    def test_title_required(self, client, admin_headers):
        response = client.post(
            "/api/admin/content/category", json={"title": "   "}, headers=admin_headers
        )
        assert response.status_code == 400

    def test_reorder_persists(self, client, admin_headers):
        ids = []
        for title in ("News", "Civic", "Commerce"):
            ids.append(
                client.post(
                    "/api/admin/content/category", json={"title": title}, headers=admin_headers
                ).get_json()["id"]
            )
        response = client.post(
            "/api/admin/content/category/reorder",
            json={"ids": list(reversed(ids))},
            headers=admin_headers,
        )
        assert response.status_code == 200
        assert [i["title"] for i in response.get_json()["items"]] == ["Commerce", "Civic", "News"]


class TestVisibilityAndScheduling:
    def test_draft_is_hidden_from_public(self, client, admin_headers):
        client.post(
            "/api/admin/content/story",
            json={"title": "Draft", "isPublished": False},
            headers=admin_headers,
        )
        assert client.get("/api/content").get_json()["stories"] == []

    def test_published_story_is_public(self, client, admin_headers):
        client.post(
            "/api/admin/content/story",
            json={"title": "Live", "isPublished": True, "isVisible": True},
            headers=admin_headers,
        )
        assert len(client.get("/api/content").get_json()["stories"]) == 1

    def test_inactive_item_is_hidden(self, client, admin_headers):
        client.post(
            "/api/admin/content/story",
            json={"title": "Live", "isPublished": True, "isVisible": True, "isActive": False},
            headers=admin_headers,
        )
        assert client.get("/api/content").get_json()["stories"] == []

    def test_future_schedule_is_hidden(self, client, admin_headers):
        client.post(
            "/api/admin/content/hero",
            json={"title": "Future", "isPublished": True, "startsAt": "2099-01-01T00:00:00Z"},
            headers=admin_headers,
        )
        assert client.get("/api/content").get_json()["hero"] == []

    def test_past_schedule_is_public(self, client, admin_headers):
        client.post(
            "/api/admin/content/hero",
            json={"title": "Now", "isPublished": True, "startsAt": "2020-01-01T00:00:00Z"},
            headers=admin_headers,
        )
        assert len(client.get("/api/content").get_json()["hero"]) == 1

    def test_expired_schedule_is_hidden(self, client, admin_headers):
        client.post(
            "/api/admin/content/hero",
            json={
                "title": "Expired",
                "isPublished": True,
                "startsAt": "2020-01-01T00:00:00Z",
                "endsAt": "2021-01-01T00:00:00Z",
            },
            headers=admin_headers,
        )
        assert client.get("/api/content").get_json()["hero"] == []


class TestContentValidation:
    def test_invalid_source_type_rejected(self, client, admin_headers):
        response = client.post(
            "/api/admin/content/story",
            json={"title": "x", "sourceType": "BOGUS"},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_invalid_action_type_rejected(self, client, admin_headers):
        response = client.post(
            "/api/admin/content/action",
            json={"title": "x", "actionType": "NOPE"},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_invalid_transition_rejected(self, client, admin_headers):
        response = client.post(
            "/api/admin/content/hero",
            json={"title": "x", "transitionType": "explode"},
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_unknown_content_type_404(self, client, admin_headers):
        assert client.get("/api/admin/content/nope", headers=admin_headers).status_code == 404


class TestLiveDataContract:
    def test_story_exposes_source_contract(self, client, admin_headers):
        client.post(
            "/api/admin/content/story",
            json={
                "title": "Market update",
                "isPublished": True,
                "isVisible": True,
                "sourceType": "LIVE",
                "source": "SautiPay Engine",
                "sourceUrl": "https://example.com",
            },
            headers=admin_headers,
        )
        story = client.get("/api/content").get_json()["stories"][0]
        for key in (
            "id",
            "title",
            "description",
            "image",
            "category",
            "source",
            "sourceUrl",
            "publishedAt",
            "sourceType",
            "isFeatured",
            "isVisible",
        ):
            assert key in story
        assert story["sourceType"] == "LIVE"


class TestMediaLibrary:
    def test_upload_requires_token(self, client):
        response = client.post(
            "/api/media/upload", data=png_upload(), content_type="multipart/form-data"
        )
        assert response.status_code == 401

    def test_upload_and_serve(self, client, admin_headers):
        response = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        )
        assert response.status_code == 201
        media_id = response.get_json()["id"]
        assert client.get(f"/api/media/{media_id}").status_code == 200

    def test_duplicate_upload_is_reused(self, client, admin_headers):
        first = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        )
        second = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        )
        assert second.status_code == 200
        assert first.get_json()["id"] == second.get_json()["id"]

    def test_rejected_extension(self, client, admin_headers):
        response = client.post(
            "/api/media/upload",
            data={"file": (io.BytesIO(b"x"), "payload.exe")},
            content_type="multipart/form-data",
            headers=admin_headers,
        )
        assert response.status_code == 400

    def test_referenced_media_cannot_be_deleted(self, client, admin_headers):
        media_id = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        ).get_json()["id"]
        client.post(
            "/api/admin/content/category",
            json={"title": "Civic", "imageId": media_id},
            headers=admin_headers,
        )
        response = client.delete(f"/api/media/{media_id}", headers=admin_headers)
        assert response.status_code == 409
        assert "categories" in response.get_json()["references"]

    def test_unused_media_can_be_deleted(self, client, admin_headers):
        media_id = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        ).get_json()["id"]
        assert client.delete(f"/api/media/{media_id}", headers=admin_headers).status_code == 200

    def test_private_media_is_not_public(self, client, admin_headers):
        media_id = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        ).get_json()["id"]
        client.patch(f"/api/media/{media_id}", json={"is_public": False}, headers=admin_headers)
        assert client.get(f"/api/media/{media_id}").status_code == 403

    def test_admin_payload_exposes_raw_media_ids(self, client, admin_headers):
        """The editor round-trips imageId; without it a save would clear the image."""
        media_id = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        ).get_json()["id"]
        created = client.post(
            "/api/admin/content/hero",
            json={"title": "With image", "imageId": media_id, "isPublished": True},
            headers=admin_headers,
        ).get_json()
        assert created["imageId"] == media_id

        fetched = client.get(
            f"/api/admin/content/hero/{created['id']}", headers=admin_headers
        ).get_json()
        assert fetched["imageId"] == media_id
        assert fetched["image"]["id"] == media_id

    def test_editing_text_preserves_image_association(self, client, admin_headers):
        media_id = client.post(
            "/api/media/upload",
            data=png_upload(),
            content_type="multipart/form-data",
            headers=admin_headers,
        ).get_json()["id"]
        created = client.post(
            "/api/admin/content/hero",
            json={"title": "Original title", "imageId": media_id},
            headers=admin_headers,
        ).get_json()

        # A text-only edit must not detach the image.
        updated = client.patch(
            f"/api/admin/content/hero/{created['id']}",
            json={"title": "Updated title"},
            headers=admin_headers,
        ).get_json()
        assert updated["title"] == "Updated title"
        assert updated["imageId"] == media_id


class TestDataSafety:
    def test_existing_conversations_survive_content_workflow(self, client, admin_headers, app):
        from backend.models import Conversation, Message, User

        with app.app_context():
            user = User(phone_number="+254700999888", name="Existing")
            db.session.add(user)
            db.session.commit()
            conversation = Conversation(user_id=user.id)
            db.session.add(conversation)
            db.session.commit()
            db.session.add(Message(conversation_id=conversation.id, role="user", content="hi"))
            db.session.commit()
            user_id = user.id
            conversation_id = conversation.id

        client.post("/api/admin/content/category", json={"title": "News"}, headers=admin_headers)
        client.post("/api/admin/content/hero", json={"title": "Hero"}, headers=admin_headers)

        with app.app_context():
            assert db.session.get(User, user_id) is not None
            assert db.session.get(Conversation, conversation_id) is not None
            assert len(db.session.get(Conversation, conversation_id).messages) == 1
            assert MediaAsset.query.count() == 0
            assert Category.query.count() == 1
            assert HeroSlide.query.count() == 1
            assert Story.query.count() == 0
