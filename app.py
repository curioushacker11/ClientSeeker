import os
import re
import sqlite3
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
DATABASE = os.path.join(app.instance_path, "leads.db")
SCRAPER_URL = os.environ.get("SCRAPER_URL", "http://localhost:8001")


# --- Database ---

def get_db():
    if "db" not in g:
        os.makedirs(app.instance_path, exist_ok=True)
        g.db = sqlite3.connect(DATABASE)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(exc):
    db = g.pop("db", None)
    if db:
        db.close()


def init_db():
    db = get_db()
    db.execute("""
        CREATE TABLE IF NOT EXISTS leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            platform TEXT NOT NULL,
            handle TEXT NOT NULL,
            profile_url TEXT NOT NULL,
            business_name TEXT,
            address TEXT,
            phone TEXT,
            website TEXT,
            rating REAL,
            maps_url TEXT,
            lat REAL,
            lng REAL,
            status TEXT DEFAULT 'new',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.commit()


with app.app_context():
    init_db()


# --- URL Parsing ---

PLATFORM_PATTERNS = {
    "tiktok": [
        r"tiktok\.com/@([^/?#]+)",
        r"tiktok\.com/([^/?#]+)",
    ],
    "instagram": [
        r"instagram\.com/([^/?#]+)",
    ],
    "facebook": [
        r"facebook\.com/([^/?#]+)",
        r"fb\.com/([^/?#]+)",
    ],
}

IGNORE_SLUGS = {"reel", "reels", "p", "stories", "live", "video", "photo", "watch", "share", "explore"}


def parse_social_url(url):
    """Extract platform and handle from a social media URL."""
    url = url.strip()
    if not url.startswith("http"):
        url = "https://" + url

    for platform, patterns in PLATFORM_PATTERNS.items():
        for pattern in patterns:
            match = re.search(pattern, url, re.IGNORECASE)
            if match:
                handle = match.group(1).strip("@").lower()
                if handle not in IGNORE_SLUGS and handle not in ("profile.php",):
                    return platform, handle, url
    return None, None, url


def handle_to_search_name(handle):
    """Convert a social media handle to a more searchable name."""
    name = handle.replace("_", " ").replace(".", " ").replace("-", " ")
    # Remove trailing numbers that are likely not part of the name
    name = re.sub(r"\d+$", "", name).strip()
    return name


# --- Google Maps Scraper ---

def search_maps(query):
    """Search Google Maps via the local scraper service."""
    try:
        resp = requests.get(
            f"{SCRAPER_URL}/scrape-get",
            params={"query": query, "max_places": 1, "lang": "en", "headless": True, "concurrency": 1},
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return None, "No results found"
        return results[0], None
    except requests.ConnectionError:
        return None, "Scraper not running. Start it with: docker-compose -f scraper/docker-compose.yml up"
    except requests.RequestException as e:
        return None, str(e)


# --- Routes ---

@app.route("/")
def index():
    # Check if scraper is reachable
    scraper_ok = False
    try:
        r = requests.get(f"{SCRAPER_URL}/", timeout=3)
        scraper_ok = r.ok
    except Exception:
        pass
    return render_template("index.html", scraper_ok=scraper_ok)


@app.route("/api/search", methods=["POST"])
def search():
    data = request.json or {}
    url = data.get("url", "").strip()
    custom_query = data.get("custom_query", "").strip()

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    platform, handle, full_url = parse_social_url(url)
    if not handle:
        return jsonify({"error": "Could not extract handle from URL. Supported: TikTok, Instagram, Facebook"}), 400

    search_name = custom_query if custom_query else handle_to_search_name(handle)
    place, err = search_maps(search_name)

    result = {
        "platform": platform,
        "handle": handle,
        "profile_url": full_url,
        "search_query": search_name,
    }

    if place:
        result["found"] = True
        result["business_name"] = place.get("name", "")
        result["address"] = place.get("address", "")
        result["phone"] = place.get("phone", "")
        result["website"] = place.get("website", "")
        result["rating"] = place.get("rating")
        result["maps_url"] = place.get("link", "")
        coords = place.get("coordinates", {})
        result["lat"] = coords.get("latitude")
        result["lng"] = coords.get("longitude")
        result["action"] = "visit"
    else:
        result["found"] = False
        result["error"] = err
        result["action"] = "message"

    return jsonify(result)


@app.route("/api/leads", methods=["POST"])
def save_lead():
    data = request.json or {}
    db = get_db()
    db.execute(
        """INSERT INTO leads
           (platform, handle, profile_url, business_name, address, phone, website, rating, maps_url, lat, lng, status, notes)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (
            data.get("platform", ""),
            data.get("handle", ""),
            data.get("profile_url", ""),
            data.get("business_name", ""),
            data.get("address", ""),
            data.get("phone", ""),
            data.get("website", ""),
            data.get("rating"),
            data.get("maps_url", ""),
            data.get("lat"),
            data.get("lng"),
            data.get("status", "new"),
            data.get("notes", ""),
        ),
    )
    db.commit()
    return jsonify({"saved": True})


@app.route("/api/leads", methods=["GET"])
def get_leads():
    db = get_db()
    status_filter = request.args.get("status")
    if status_filter:
        rows = db.execute("SELECT * FROM leads WHERE status = ? ORDER BY created_at DESC", (status_filter,)).fetchall()
    else:
        rows = db.execute("SELECT * FROM leads ORDER BY created_at DESC").fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads/<int:lead_id>", methods=["PATCH"])
def update_lead(lead_id):
    data = request.json or {}
    db = get_db()
    fields = []
    values = []
    for key in ("status", "notes"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if not fields:
        return jsonify({"error": "Nothing to update"}), 400
    values.append(lead_id)
    db.execute(f"UPDATE leads SET {', '.join(fields)} WHERE id = ?", values)
    db.commit()
    return jsonify({"updated": True})


@app.route("/api/leads/<int:lead_id>", methods=["DELETE"])
def delete_lead(lead_id):
    db = get_db()
    db.execute("DELETE FROM leads WHERE id = ?", (lead_id,))
    db.commit()
    return jsonify({"deleted": True})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
