"""Populate SautiPay with CMS content for local development and visual review.

This is a developer convenience script. It creates media assets and content
items through the same admin API the dashboard uses, so nothing bypasses
validation or authorization. Running it twice is safe: uploads are
de-duplicated by checksum and content items are matched by title.
"""
import io
import json
import os
import struct
import sys
import urllib.error
import urllib.request
import zlib

BASE = os.environ.get("SAUTIPAY_API", "http://localhost:8000")
TOKEN = os.environ.get("ADMIN_TOKEN", "")


def call(path, method="GET", body=None, token=TOKEN):
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(BASE + path, data=data, method=method)
    request.add_header("Content-Type", "application/json")
    if token:
        request.add_header("X-Admin-Token", token)
    try:
        with urllib.request.urlopen(request) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as error:
        print(f"  ! {method} {path} -> {error.code} {error.read().decode()[:160]}")
        raise


def solid_png(width, height, rgb):
    """Build a small solid-colour PNG without external image dependencies."""
    rows = b""
    for _ in range(height):
        rows += b"\x00" + bytes(rgb) * width

    def chunk(tag, data):
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    return (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows))
        + chunk(b"IEND", b"")
    )


def upload(name, width, height, rgb):
    png = solid_png(width, height, rgb)
    boundary = "----sautipay"
    body = (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{name}"\r\n'
        "Content-Type: image/png\r\n\r\n"
    ).encode() + png + f"\r\n--{boundary}--\r\n".encode()
    request = urllib.request.Request(f"{BASE}/api/media/upload", data=body, method="POST")
    request.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
    request.add_header("X-Admin-Token", TOKEN)
    with urllib.request.urlopen(request) as response:
        return json.loads(response.read())


def upsert(content_type, title, payload):
    """Create the item if its title is not already present."""
    existing = call(f"/api/admin/content/{content_type}").get("items", [])
    match = next((item for item in existing if item.get("title") == title), None)
    if match:
        return call(
            f"/api/admin/content/{content_type}/{match['id']}",
            "PATCH",
            payload,
        )
    return call(f"/api/admin/content/{content_type}", "POST", payload)


def main():
    if not TOKEN:
        print("ADMIN_TOKEN is required")
        return 1

    print("Uploading media…")
    hero = upload("hero-kenya.png", 1600, 900, (46, 110, 84))
    news = upload("category-news.png", 400, 400, (45, 95, 138))
    agriculture = upload("category-agriculture.png", 400, 400, (47, 143, 91))
    mpesa = upload("category-mpesa.png", 400, 400, (47, 179, 47))
    story_markets = upload("story-markets.png", 800, 600, (226, 104, 58))
    story_civic = upload("story-civic.png", 800, 600, (74, 85, 102))
    featured = upload("featured-harvest.png", 1400, 800, (201, 85, 42))

    print("Publishing hero…")
    upsert("hero", "Your country, answered clearly", {
        "eyebrow": "Kenya, in your language",
        "title": "Your country, answered clearly",
        "subtitle": "News, prices, services and guidance you can act on.",
        "description": "SautiPay helps you find trusted information in the language you speak at home.",
        "imageId": hero["id"],
        "ctaLabel": "Start a conversation",
        "ctaTarget": "#ask",
        "displayDuration": 7000,
        "transitionType": "fade",
        "isPublished": True,
        "isActive": True,
    })

    print("Publishing categories…")
    for title, description, icon, image, accent in [
        ("News", "What is happening across Kenya today", "news", news["id"], "#2d5f8a"),
        ("Civic", "Government services and your rights", "civic", None, "#2d5f8a"),
        ("Agriculture", "Prices, crops and farming advice", "agriculture", agriculture["id"], "#2f8f5b"),
        ("Commerce", "Trusted vendors and products", "commerce", None, "#e2683a"),
        ("M-Pesa", "Balances, transfers and payments", "mpesa", mpesa["id"], "#2f8f5b"),
    ]:
        upsert("category", title, {
            "title": title,
            "description": description,
            "icon": icon,
            "imageId": image,
            "accent": accent,
            "isPublished": True,
            "isActive": True,
        })

    print("Publishing stories…")
    upsert("story", "Maize prices steady as harvest season begins", {
        "title": "Maize prices steady as harvest season begins",
        "description": "Wholesale maize traded within a narrow band in major markets this week.",
        "imageId": story_markets["id"],
        "category": "Agriculture",
        "source": "SautiPay Markets",
        "publishedAt": "2026-09-25T06:00:00Z",
        "sourceType": "LIVE",
        "isVisible": True,
        "isPublished": True,
        "isFeatured": True,
    })
    upsert("story", "County assemblies adopt new service charters", {
        "title": "County assemblies adopt new service charters",
        "description": "Counties outline revised timelines for key citizen services.",
        "imageId": story_civic["id"],
        "category": "Civic",
        "source": "Nation Media",
        "sourceUrl": "https://nation.africa",
        "publishedAt": "2026-09-24T09:30:00Z",
        "sourceType": "MANUAL",
        "isVisible": True,
        "isPublished": True,
    })

    print("Publishing quick actions…")
    for title, description, icon, action_type in [
        ("Check M-Pesa balance", "See your balance instantly", "mpesa", "START_MPESA_FLOW"),
        ("Get market prices", "Today’s maize, beans and tea prices", "agriculture", "OPEN_CHAT"),
        ("Find trusted vendors", "Verified sellers near you", "commerce", "OPEN_COMMERCE"),
        ("Read government notices", "Official public notices", "civic", "OPEN_PAGE"),
        ("Ask SautiPay", "Ask anything at all", "spark", "OPEN_CHAT"),
    ]:
        upsert("action", title, {
            "title": title,
            "description": description,
            "icon": icon,
            "actionType": action_type,
            "isPublished": True,
            "isActive": True,
        })

    print("Publishing featured content…")
    upsert("featured", "Harvest season: plan better with SautiPay", {
        "title": "Harvest season: plan better with SautiPay",
        "description": "Track regional crop prices, rainfall outlook and market demand so you can plan your season with confidence.",
        "imageId": featured["id"],
        "ctaLabel": "Explore agriculture",
        "ctaTarget": "#agriculture",
        "isPublished": True,
        "isActive": True,
    })

    public = call("/api/content", token=None)
    print("\nPublic content:", {k: len(v) for k, v in public.items()})
    return 0


if __name__ == "__main__":
    sys.exit(main())
