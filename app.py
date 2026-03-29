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
DEFAULT_REGION = "Costa Rica"


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

def search_maps(query, max_results=1):
    """Search Google Maps via the local scraper service."""
    try:
        resp = requests.get(
            f"{SCRAPER_URL}/scrape-get",
            params={"query": query, "max_places": max_results, "lang": "es", "headless": True, "concurrency": 1},
            timeout=60,
        )
        resp.raise_for_status()
        results = resp.json()
        if not results:
            return [], "No results found"
        return results, None
    except requests.ConnectionError:
        return [], "Scraper not running. Start it with: docker-compose -f scraper/docker-compose.yml up"
    except requests.RequestException as e:
        return [], str(e)


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


def _place_to_dict(place):
    coords = place.get("coordinates", {})
    return {
        "business_name": place.get("name", ""),
        "address": place.get("address", ""),
        "phone": place.get("phone", ""),
        "website": place.get("website", ""),
        "rating": place.get("rating"),
        "maps_url": place.get("link", ""),
        "lat": coords.get("latitude"),
        "lng": coords.get("longitude"),
    }


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
    query_with_region = f"{search_name} {DEFAULT_REGION}"
    places, err = search_maps(query_with_region, max_results=3)

    result = {
        "platform": platform,
        "handle": handle,
        "profile_url": full_url,
        "search_query": search_name,
    }

    if places:
        result["found"] = True
        result["candidates"] = [_place_to_dict(p) for p in places]
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
    for key in ("status", "notes", "business_name", "maps_url", "address", "lat", "lng"):
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


# --- Bulk Search ---

@app.route("/api/bulk-search", methods=["POST"])
def bulk_search():
    """Process a single URL from the bulk queue. Called repeatedly by the frontend."""
    data = request.json or {}
    url = data.get("url", "").strip()
    auto_save = data.get("auto_save", True)

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    platform, handle, full_url = parse_social_url(url)
    if not handle:
        return jsonify({"skipped": True, "url": url, "reason": "Could not extract handle"}), 200

    search_name = handle_to_search_name(handle)
    query_with_region = f"{search_name} {DEFAULT_REGION}"
    places, err = search_maps(query_with_region, max_results=1)

    result = {
        "platform": platform,
        "handle": handle,
        "profile_url": full_url,
        "search_query": search_name,
    }

    if places:
        place = places[0]
        result.update(_place_to_dict(place))
        result["found"] = True
        result["action"] = "visit"
        if auto_save:
            result["status"] = "to_visit"
            _save_lead(result)
    else:
        result["found"] = False
        result["error"] = err
        result["action"] = "message"
        if auto_save:
            result["status"] = "to_message"
            _save_lead(result)

    return jsonify(result)


def _save_lead(data):
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


# --- Route Optimizer ---

@app.route("/api/optimize-route", methods=["POST"])
def optimize_route():
    """Use OSRM to find optimal visit order for leads with coordinates."""
    data = request.json or {}
    lead_ids = data.get("lead_ids", [])
    start_lat = data.get("start_lat")
    start_lng = data.get("start_lng")

    db = get_db()
    if lead_ids:
        placeholders = ",".join("?" for _ in lead_ids)
        rows = db.execute(
            f"SELECT * FROM leads WHERE id IN ({placeholders}) AND lat IS NOT NULL AND lng IS NOT NULL",
            lead_ids,
        ).fetchall()
    else:
        rows = db.execute(
            "SELECT * FROM leads WHERE status = 'to_visit' AND lat IS NOT NULL AND lng IS NOT NULL"
        ).fetchall()

    leads = [dict(r) for r in rows]
    if not leads:
        return jsonify({"error": "No leads with coordinates found"}), 400

    # Build coordinates list: start point first (if provided), then all leads
    coords = []
    if start_lat and start_lng:
        coords.append({"lng": float(start_lng), "lat": float(start_lat), "is_start": True})
    for lead in leads:
        coords.append({"lng": lead["lng"], "lat": lead["lat"], "lead": lead})

    if len(coords) < 2:
        return jsonify({"error": "Need at least 2 points for a route"}), 400

    # Call OSRM trip endpoint (solves Traveling Salesman Problem)
    coord_str = ";".join(f"{c['lng']},{c['lat']}" for c in coords)
    source_param = "first" if start_lat else "any"

    try:
        resp = requests.get(
            f"https://router.project-osrm.org/trip/v1/driving/{coord_str}",
            params={"source": source_param, "roundtrip": "false", "geometries": "geojson", "overview": "full"},
            timeout=30,
        )
        resp.raise_for_status()
        osrm = resp.json()
    except requests.RequestException as e:
        return jsonify({"error": f"OSRM request failed: {e}"}), 500

    if osrm.get("code") != "Ok":
        return jsonify({"error": f"OSRM error: {osrm.get('message', 'unknown')}"}), 500

    trip = osrm["trips"][0]
    waypoint_order = [w["waypoint_index"] for w in osrm["waypoints"]]

    # Build ordered list of leads based on OSRM's optimal order
    ordered_leads = []
    for idx in waypoint_order:
        c = coords[idx]
        if "lead" in c:
            ordered_leads.append(c["lead"])

    # Build Google Maps directions URL with optimized order
    gmaps_waypoints = []
    for lead in ordered_leads:
        gmaps_waypoints.append(f"{lead['lat']},{lead['lng']}")

    if start_lat:
        origin = f"{start_lat},{start_lng}"
    else:
        origin = gmaps_waypoints.pop(0)

    destination = gmaps_waypoints[-1] if gmaps_waypoints else origin
    mid_waypoints = gmaps_waypoints[:-1] if len(gmaps_waypoints) > 1 else []

    gmaps_url = f"https://www.google.com/maps/dir/{origin}"
    for wp in gmaps_waypoints:
        gmaps_url += f"/{wp}"

    return jsonify({
        "ordered_leads": ordered_leads,
        "total_distance_km": round(trip["distance"] / 1000, 1),
        "total_duration_min": round(trip["duration"] / 60),
        "route_geometry": trip["geometry"],
        "google_maps_url": gmaps_url,
    })


if __name__ == "__main__":
    app.run(debug=True, port=5001)
