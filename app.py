import html
import os
import re
import sqlite3
import time
from datetime import datetime

import requests
from dotenv import load_dotenv
from flask import Flask, g, jsonify, render_template, request

load_dotenv()

app = Flask(__name__)
DATABASE = os.path.join(app.instance_path, "leads.db")
SCRAPER_URL = os.environ.get("SCRAPER_URL", "http://localhost:8001")


# --- Zone Definitions ---

CENTRAL_PROVINCES = {"Alajuela", "Heredia", "Cartago"}

# San José cantons EXCLUDED from Central zone
SJ_EXCLUDED_CANTONS = {
    "Pérez Zeledón", "Perez Zeledón", "Pérez Zeledon", "Perez Zeledon",
    "Puriscal", "Tarrazú", "Tarrazu", "Mora", "Turrubares",
    "León Cortés Castro", "Leon Cortés Castro", "León Cortes Castro",
    "Leon Cortes Castro", "León Cortés", "Leon Cortés",
}

PEREZ_ZELEDON_NAMES = {
    "Pérez Zeledón", "Perez Zeledón", "Pérez Zeledon", "Perez Zeledon",
}


def classify_zone(province, canton):
    """Classify a location into a route zone based on province and canton."""
    if not province:
        return "Unclassified"
    # Normalize
    prov = province.strip()
    cant = (canton or "").strip()

    if prov in CENTRAL_PROVINCES:
        return "Central"
    if prov in ("San José", "San Jose", "Provincia de San José", "Provincia de San Jose"):
        if cant in SJ_EXCLUDED_CANTONS:
            if cant in PEREZ_ZELEDON_NAMES:
                return "Pérez Zeledón"
            return "Unclassified"
        return "Central"
    return "Unclassified"


def reverse_geocode(lat, lng):
    """Get province and canton from coordinates using Nominatim."""
    try:
        resp = requests.get(
            "https://nominatim.openstreetmap.org/reverse",
            params={"lat": lat, "lon": lng, "format": "json", "zoom": 10,
                    "accept-language": "es"},
            headers={"User-Agent": "ClientSeeker/1.0"},
            timeout=10,
        )
        resp.raise_for_status()
        data = resp.json()
        addr = data.get("address", {})
        # Costa Rica: Nominatim uses "province" not "state", and "city_district" for canton
        province = addr.get("province", "") or addr.get("state", "")
        canton = addr.get("city_district", "") or addr.get("county", "")
        return province, canton
    except Exception:
        return "", ""


def classify_lead_zone(lat, lng):
    """Reverse geocode and classify a lead's zone. Returns zone string."""
    if lat is None or lng is None:
        return "Unclassified"
    province, canton = reverse_geocode(lat, lng)
    return classify_zone(province, canton)


# --- Follower Count ---

SCRAPE_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Accept-Language": "en-US,en;q=0.9,es;q=0.8",
}


def fetch_followers(platform, profile_url):
    """Try to fetch follower count from the social media profile page."""
    if not profile_url:
        return None
    try:
        resp = requests.get(profile_url, headers=SCRAPE_HEADERS, timeout=15)
        text = resp.text

        if platform == "tiktok":
            match = re.search(r'"followerCount"\s*:\s*(\d+)', text)
            if match:
                return int(match.group(1))

        elif platform == "instagram":
            # Meta tag: "1,234 Followers" or "1234 Followers"
            match = re.search(
                r'content="([0-9][0-9,\.KMkm]*)\s+[Ff]ollowers', text)
            if match:
                return _parse_follower_string(match.group(1))
            # JSON: "edge_followed_by":{"count":1234}
            match = re.search(r'"edge_followed_by"\s*:\s*\{\s*"count"\s*:\s*(\d+)', text)
            if match:
                return int(match.group(1))
            # Newer IG JSON: "follower_count":1234
            match = re.search(r'"follower_count"\s*:\s*(\d+)', text)
            if match:
                return int(match.group(1))

        elif platform == "facebook":
            # "1,234 followers" or "1.2K followers" in page text/meta
            match = re.search(
                r'content="([0-9][0-9,\.KMkm]*)\s+[Ff]ollowers', text)
            if match:
                return _parse_follower_string(match.group(1))
            # JSON patterns: "follower_count":1234
            match = re.search(r'"follower_count"\s*:\s*(\d+)', text)
            if match:
                return int(match.group(1))
            # "1,234 people follow this"
            match = re.search(
                r'([\d][\d,\.]*[KMkm]?)\s+people follow', text)
            if match:
                return _parse_follower_string(match.group(1))

    except Exception:
        pass
    return None


def _parse_follower_string(s):
    """Parse follower strings like '1,234', '89.2K', '1.5M' into int."""
    s = s.strip().replace(",", "")
    upper = s.upper()
    if upper.endswith("M"):
        return int(float(upper[:-1]) * 1_000_000)
    if upper.endswith("K"):
        return int(float(upper[:-1]) * 1_000)
    try:
        return int(s)
    except ValueError:
        return None


def fetch_tiktok_name(profile_url):
    """Fetch the display name (nickname) from a TikTok profile page."""
    try:
        resp = requests.get(profile_url, headers=SCRAPE_HEADERS, timeout=10)
        match = re.search(r'"nickname"\s*:\s*"([^"]+)"', resp.text)
        if match:
            return match.group(1)
    except Exception:
        pass
    return None


def format_followers(count):
    """Format follower count for display (e.g. 1500 -> '1.5K')."""
    if count is None:
        return None
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K"
    return str(count)


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
            reviewed INTEGER DEFAULT 0,
            followers INTEGER,
            zone TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    # Add columns if missing (existing databases)
    for col, typedef in [
        ("reviewed", "INTEGER DEFAULT 0"),
        ("followers", "INTEGER"),
        ("zone", "TEXT DEFAULT ''"),
        ("email", "TEXT DEFAULT ''"),
        ("email_source", "TEXT DEFAULT ''"),
        ("priority", "INTEGER DEFAULT 0"),
    ]:
        try:
            db.execute(f"ALTER TABLE leads ADD COLUMN {col} {typedef}")
        except Exception:
            pass
    db.execute("""
        CREATE TABLE IF NOT EXISTS trips (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            start_lat REAL,
            start_lng REAL,
            start_label TEXT DEFAULT '',
            notes TEXT DEFAULT '',
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    db.execute("""
        CREATE TABLE IF NOT EXISTS trip_leads (
            trip_id INTEGER NOT NULL,
            lead_id INTEGER NOT NULL,
            PRIMARY KEY (trip_id, lead_id)
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


# --- Google Maps Scraper ---

def _clean(text):
    """Decode HTML entities and strip review count / hours strings from scraper output."""
    if not text:
        return text
    text = html.unescape(str(text))
    text = re.sub(r'\s*-?\s*\d+\s*(opiniones|reviews|rese[ñn]as|comentarios)\s*$', '', text, flags=re.IGNORECASE)
    text = re.sub(r'\d+\s*(a\.m\.|p\.m\.).*$', '', text, flags=re.IGNORECASE)
    return text.strip()


def search_maps(query, max_results=1):
    """Search Google Maps via the local scraper service."""
    try:
        resp = requests.get(
            f"{SCRAPER_URL}/scrape-get",
            params={"query": query, "max_places": max_results, "lang": "es",
                    "headless": True, "concurrency": 1},
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


def _place_to_dict(place):
    coords = place.get("coordinates", {})
    return {
        "business_name": _clean(place.get("name", "")),
        "address": _clean(place.get("address", "")),
        "phone": _clean(place.get("phone", "")),
        "website": place.get("website", ""),
        "rating": place.get("rating"),
        "maps_url": place.get("link", ""),
        "lat": coords.get("latitude"),
        "lng": coords.get("longitude"),
    }


# --- Routes ---

@app.route("/")
def index():
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

    # For TikTok, fetch display name and use it for Maps search
    search_name = custom_query if custom_query else handle
    tiktok_name = None
    if platform == "tiktok" and not custom_query:
        tiktok_name = fetch_tiktok_name(full_url)
        if tiktok_name:
            search_name = tiktok_name
    query = search_name
    place, err = search_maps(query, max_results=1)

    result = {
        "platform": platform,
        "handle": handle,
        "profile_url": full_url,
        "search_query": search_name,
    }

    if place:
        result["found"] = True
        result.update(_place_to_dict(place))
        result["action"] = "visit"
        # Try to get follower count
        followers = fetch_followers(platform, full_url)
        result["followers"] = followers
        result["followers_display"] = format_followers(followers)
        # Classify zone
        result["zone"] = classify_lead_zone(result.get("lat"), result.get("lng"))
    else:
        result["found"] = False
        result["error"] = err
        result["action"] = "message"
        # Still try followers
        followers = fetch_followers(platform, full_url)
        result["followers"] = followers
        result["followers_display"] = format_followers(followers)

    return jsonify(result)


@app.route("/api/check-duplicate", methods=["POST"])
def check_duplicate():
    """Check if a lead with same handle+platform or business_name already exists."""
    data = request.json or {}
    handle = data.get("handle", "").strip().lower()
    platform = data.get("platform", "").strip().lower()
    business_name = data.get("business_name", "").strip()
    db = get_db()

    dupes = []
    if handle and platform:
        rows = db.execute(
            "SELECT id, handle, platform, business_name, status, followers FROM leads "
            "WHERE LOWER(handle) = ? AND LOWER(platform) = ?",
            (handle, platform),
        ).fetchall()
        dupes.extend([dict(r) for r in rows])

    if business_name:
        seen_ids = {d["id"] for d in dupes}
        rows = db.execute(
            "SELECT id, handle, platform, business_name, status, followers FROM leads "
            "WHERE LOWER(business_name) = LOWER(?)",
            (business_name,),
        ).fetchall()
        for r in rows:
            if r["id"] not in seen_ids:
                dupes.append(dict(r))

    return jsonify({"duplicates": dupes})


@app.route("/api/leads", methods=["POST"])
def save_lead():
    data = request.json or {}
    db = get_db()
    db.execute(
        """INSERT INTO leads
           (platform, handle, profile_url, business_name, address, phone,
            website, rating, maps_url, lat, lng, status, notes, followers, zone)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            data.get("followers"),
            data.get("zone", ""),
        ),
    )
    db.commit()
    return jsonify({"saved": True})


@app.route("/api/leads", methods=["GET"])
def get_leads():
    db = get_db()
    status_filter = request.args.get("status")
    zone_filter = request.args.get("zone")
    priority_filter = request.args.get("priority")

    query = "SELECT * FROM leads WHERE 1=1"
    params = []
    if status_filter:
        query += " AND status = ?"
        params.append(status_filter)
    if zone_filter:
        query += " AND zone = ?"
        params.append(zone_filter)
    if priority_filter:
        query += " AND priority = ?"
        params.append(int(priority_filter))
    query += " ORDER BY created_at DESC"

    rows = db.execute(query, params).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/leads/<int:lead_id>", methods=["PATCH"])
def update_lead(lead_id):
    data = request.json or {}
    db = get_db()
    fields = []
    values = []
    for key in ("status", "notes", "business_name", "maps_url", "address",
                "lat", "lng", "reviewed", "followers", "zone",
                "email", "email_source", "phone", "profile_url",
                "handle", "platform", "website", "rating", "priority"):
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
    """Process a single URL from the bulk queue."""
    data = request.json or {}
    url = data.get("url", "").strip()
    auto_save = data.get("auto_save", True)

    if not url:
        return jsonify({"error": "No URL provided"}), 400

    platform, handle, full_url = parse_social_url(url)
    if not handle:
        return jsonify({"skipped": True, "url": url, "reason": "Could not extract handle"}), 200

    # Check for duplicates
    db = get_db()
    existing = db.execute(
        "SELECT id, business_name, status FROM leads "
        "WHERE LOWER(handle) = ? AND LOWER(platform) = ?",
        (handle.lower(), platform.lower()),
    ).fetchone()
    if existing:
        return jsonify({
            "skipped": True, "duplicate": True, "url": url,
            "handle": handle, "platform": platform,
            "existing_name": existing["business_name"],
            "existing_status": existing["status"],
            "reason": f"Duplicate: already saved as '{existing['business_name']}' ({existing['status']})",
        }), 200

    # For TikTok, fetch display name and use it for Maps search
    search_name = handle
    if platform == "tiktok":
        tiktok_name = fetch_tiktok_name(full_url)
        if tiktok_name:
            search_name = tiktok_name
    query = search_name
    place, err = search_maps(query, max_results=1)

    result = {
        "platform": platform,
        "handle": handle,
        "profile_url": full_url,
        "search_query": search_name,
    }

    if place:
        result.update(_place_to_dict(place))
        result["found"] = True
        result["action"] = "visit"
        # Zone classification for bulk (skip if no coords to save time)
        # Zones are classified later via /api/classify-zones
        result["zone"] = ""
        if auto_save:
            result["status"] = "to_visit"
            _save_lead(result)
    else:
        result["found"] = False
        result["error"] = err
        result["action"] = "message"
        result["zone"] = ""
        if auto_save:
            result["status"] = "to_message"
            _save_lead(result)

    return jsonify(result)


def _save_lead(data):
    db = get_db()
    db.execute(
        """INSERT INTO leads
           (platform, handle, profile_url, business_name, address, phone,
            website, rating, maps_url, lat, lng, status, notes, followers, zone)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
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
            data.get("followers"),
            data.get("zone", ""),
        ),
    )
    db.commit()


# --- Zone Classification ---

def resolve_short_url(short_url):
    """Resolve a shortened URL (maps.app.goo.gl, etc.) to the full URL."""
    try:
        resp = requests.head(short_url, allow_redirects=True, timeout=10)
        return resp.url
    except Exception:
        return short_url


def extract_coords_from_maps_url(maps_url):
    """Try to extract lat/lng from a Google Maps URL. Resolves short URLs."""
    if not maps_url:
        return None, None
    # Resolve short URLs first
    if "goo.gl" in maps_url or "bit.ly" in maps_url:
        maps_url = resolve_short_url(maps_url)
    # Pattern: /@lat,lng or /place/.../@lat,lng
    match = re.search(r'@(-?\d+\.\d+),(-?\d+\.\d+)', maps_url)
    if match:
        return float(match.group(1)), float(match.group(2))
    # Pattern: !3d=lat!4d=lng (in data params)
    match = re.search(r'!3d(-?\d+\.\d+)!4d(-?\d+\.\d+)', maps_url)
    if match:
        return float(match.group(1)), float(match.group(2))
    return None, None


@app.route("/api/classify-zones", methods=["POST"])
def classify_zones():
    """Backfill coordinates from Maps URLs, then reverse geocode and classify zones."""
    db = get_db()

    # Step 1: Extract coords from Maps URLs for leads that have a URL but no coords
    url_rows = db.execute(
        "SELECT id, maps_url FROM leads "
        "WHERE maps_url IS NOT NULL AND maps_url != '' "
        "AND (lat IS NULL OR lng IS NULL)"
    ).fetchall()

    coords_filled = 0
    for row in url_rows:
        lat, lng = extract_coords_from_maps_url(row["maps_url"])
        if lat is not None:
            db.execute("UPDATE leads SET lat = ?, lng = ? WHERE id = ?",
                       (lat, lng, row["id"]))
            coords_filled += 1
    db.commit()

    # Step 2: Reverse geocode and classify leads with coords but no zone or "Unclassified"
    geo_rows = db.execute(
        "SELECT id, lat, lng FROM leads WHERE lat IS NOT NULL AND lng IS NOT NULL "
        "AND (zone IS NULL OR zone = '' OR zone = 'Unclassified')"
    ).fetchall()

    classified = 0
    for row in geo_rows:
        province, canton = reverse_geocode(row["lat"], row["lng"])
        zone = classify_zone(province, canton)
        db.execute("UPDATE leads SET zone = ? WHERE id = ?", (zone, row["id"]))
        classified += 1
        time.sleep(1.1)  # Nominatim rate limit: 1 req/sec

    db.commit()
    return jsonify({"coords_filled": coords_filled, "classified": classified})


@app.route("/api/zones", methods=["GET"])
def get_zones():
    """Return list of zones that have to_visit leads."""
    db = get_db()
    rows = db.execute(
        "SELECT DISTINCT zone FROM leads WHERE zone IS NOT NULL AND zone != '' "
        "AND status = 'to_visit' ORDER BY zone"
    ).fetchall()
    return jsonify([r["zone"] for r in rows])


# --- Follower Fetch ---

@app.route("/api/fetch-followers/<int:lead_id>", methods=["POST"])
def fetch_followers_for_lead(lead_id):
    """Try to fetch follower count for a single lead."""
    db = get_db()
    row = db.execute("SELECT platform, profile_url FROM leads WHERE id = ?", (lead_id,)).fetchone()
    if not row:
        return jsonify({"error": "Lead not found"}), 404

    count = fetch_followers(row["platform"], row["profile_url"])
    if count is not None:
        db.execute("UPDATE leads SET followers = ? WHERE id = ?", (count, lead_id))
        db.commit()
    return jsonify({"followers": count, "display": format_followers(count)})


@app.route("/api/followers-queue", methods=["GET"])
def followers_queue():
    """Return list of leads needing follower count fetch."""
    db = get_db()
    rows = db.execute(
        "SELECT id, handle, business_name, platform FROM leads "
        "WHERE (followers IS NULL OR followers = 0) "
        "AND platform IN ('tiktok', 'instagram', 'facebook') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/fix-tiktok-names", methods=["POST"])
def fix_tiktok_names():
    """Fetch real display names from TikTok and re-search Maps for all TikTok leads."""
    db = get_db()
    rows = db.execute(
        "SELECT id, handle, profile_url, business_name FROM leads "
        "WHERE platform = 'tiktok'"
    ).fetchall()

    fixed = 0
    skipped = 0
    failed = 0
    for row in rows:
        bname = (row["business_name"] or "").strip()
        handle = (row["handle"] or "").strip()
        if bname and bname.lower() != handle.lower():
            skipped += 1
            continue
        name = fetch_tiktok_name(row["profile_url"])
        if name:
            db.execute("UPDATE leads SET business_name = ? WHERE id = ?",
                       (name, row["id"]))
            fixed += 1
        else:
            failed += 1
        time.sleep(1)
    db.commit()
    return jsonify({"fixed": fixed, "skipped": skipped,
                    "failed": failed, "total": len(rows)})


@app.route("/api/re-search/<int:lead_id>", methods=["POST"])
def re_search_lead(lead_id):
    """Re-search Google Maps using the lead's current business_name."""
    db = get_db()
    row = db.execute(
        "SELECT id, business_name, handle FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Lead not found"}), 404

    search_name = row["business_name"] or row["handle"]
    place, err = search_maps(search_name, max_results=1)

    if not place:
        return jsonify({"found": False, "error": err or "No results"})

    data = _place_to_dict(place)
    zone = classify_lead_zone(data.get("lat"), data.get("lng"))
    db.execute(
        "UPDATE leads SET address=?, phone=?, website=?, rating=?, "
        "maps_url=?, lat=?, lng=?, zone=? WHERE id=?",
        (data["address"], data["phone"], data["website"], data["rating"],
         data["maps_url"], data["lat"], data["lng"], zone, lead_id),
    )
    db.commit()
    return jsonify({"found": True, "data": data, "zone": zone})


# --- Email Discovery ---

def _discover_email(lead_row):
    """
    Multi-phase email discovery for a single lead.
    Returns (email, source) or (None, None).
    Phases:
      1. Scrape social profile (email in bio, business email)
      2. Follow bio links / website (crawl for emails)
      3. Google search "{display_name} Costa Rica email"
    """
    profile_url = lead_row["profile_url"]
    platform = lead_row["platform"]
    business_name = (lead_row["business_name"] or "").strip()
    handle = (lead_row["handle"] or "").strip()
    display_name = business_name or handle

    # Phase 1: Scrape social profile
    try:
        resp = requests.post(
            f"{SCRAPER_URL}/scrape-profile",
            json={"url": profile_url, "platform": platform},
            timeout=35,
        )
        if resp.ok:
            data = resp.json()
            if data.get("emails"):
                return data["emails"][0], f"{platform}_profile"
            # Update display_name if we got one from the profile
            if data.get("display_name") and not business_name:
                display_name = data["display_name"]
            # Collect links for Phase 2
            bio_links = data.get("links", [])
        else:
            bio_links = []
    except Exception:
        bio_links = []

    time.sleep(1)

    # Phase 2: Follow bio links / lead's website
    urls_to_check = list(bio_links)
    lead_website = (lead_row["website"] or "").strip()
    if lead_website and lead_website not in urls_to_check:
        urls_to_check.append(lead_website)

    for link_url in urls_to_check[:3]:  # Max 3 links
        try:
            resp = requests.post(
                f"{SCRAPER_URL}/scrape-page",
                json={"url": link_url},
                timeout=35,
            )
            if resp.ok:
                data = resp.json()
                if data.get("emails"):
                    source = "website"
                    if "linktr" in link_url.lower():
                        source = "linktree"
                    return data["emails"][0], source
        except Exception:
            pass
        time.sleep(1)

    # Phase 3: Google search
    queries = []
    if display_name:
        queries.append(f'"{display_name}" Costa Rica email')
        queries.append(f'"{display_name}" Costa Rica contacto correo')
    if handle and handle != display_name:
        queries.append(f'"{handle}" email contacto')

    for q in queries[:2]:  # Max 2 searches
        try:
            resp = requests.post(
                f"{SCRAPER_URL}/google-search",
                json={"query": q, "max_results": 5},
                timeout=35,
            )
            if resp.ok:
                data = resp.json()
                # Check emails found in snippets
                if data.get("emails_in_snippets"):
                    return data["emails_in_snippets"][0], "google_search"
                # Try scraping top results that look like business pages
                for r in data.get("results", [])[:2]:
                    result_url = r.get("url", "")
                    # Skip social media and Google domains
                    if any(d in result_url for d in [
                        "facebook.com", "instagram.com", "tiktok.com",
                        "google.com", "youtube.com", "twitter.com",
                    ]):
                        continue
                    try:
                        page_resp = requests.post(
                            f"{SCRAPER_URL}/scrape-page",
                            json={"url": result_url},
                            timeout=35,
                        )
                        if page_resp.ok and page_resp.json().get("emails"):
                            return page_resp.json()["emails"][0], "google_result"
                    except Exception:
                        pass
                    time.sleep(1)
        except Exception:
            pass
        time.sleep(1)

    return None, None


@app.route("/api/find-email/<int:lead_id>", methods=["POST"])
def find_email(lead_id):
    """Run email discovery pipeline for a single lead."""
    db = get_db()
    row = db.execute(
        "SELECT id, platform, handle, profile_url, business_name, website, email "
        "FROM leads WHERE id = ?", (lead_id,)
    ).fetchone()
    if not row:
        return jsonify({"error": "Lead not found"}), 404

    email, source = _discover_email(row)
    if email:
        db.execute(
            "UPDATE leads SET email = ?, email_source = ? WHERE id = ?",
            (email, source, lead_id),
        )
        db.commit()
    return jsonify({"email": email, "source": source})


@app.route("/api/email-queue", methods=["GET"])
def email_queue():
    """Return list of leads needing email discovery."""
    db = get_db()
    rows = db.execute(
        "SELECT id, handle, business_name, platform FROM leads "
        "WHERE (email IS NULL OR email = '') "
        "AND platform IN ('tiktok', 'instagram', 'facebook') "
        "ORDER BY created_at DESC"
    ).fetchall()
    return jsonify([dict(r) for r in rows])


# --- Route Optimizer ---

@app.route("/api/optimize-route", methods=["POST"])
def optimize_route():
    """Use OSRM to find optimal visit order for leads with coordinates."""
    data = request.json or {}
    start_lat = data.get("start_lat")
    start_lng = data.get("start_lng")
    zone_filter = data.get("zone", "")

    db = get_db()
    query = "SELECT * FROM leads WHERE status = 'to_visit' AND lat IS NOT NULL AND lng IS NOT NULL"
    params = []
    if zone_filter:
        query += " AND zone = ?"
        params.append(zone_filter)

    rows = db.execute(query, params).fetchall()
    leads = [dict(r) for r in rows]

    if not leads:
        zone_msg = f' in zone "{zone_filter}"' if zone_filter else ""
        return jsonify({"error": f"No 'To Visit' leads with coordinates found{zone_msg}"}), 400

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
            params={"source": source_param, "roundtrip": "false",
                    "geometries": "geojson", "overview": "full"},
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

    ordered_leads = []
    for idx in waypoint_order:
        c = coords[idx]
        if "lead" in c:
            ordered_leads.append(c["lead"])

    gmaps_waypoints = [f"{lead['lat']},{lead['lng']}" for lead in ordered_leads]

    if start_lat:
        origin = f"{start_lat},{start_lng}"
    else:
        origin = gmaps_waypoints.pop(0)

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


# --- Trips ---

def _osrm_optimize(leads, start_lat=None, start_lng=None):
    """Build OSRM trip request and return (result_dict, None) or (None, error_str)."""
    coords = []
    if start_lat is not None and start_lng is not None:
        coords.append({"lng": float(start_lng), "lat": float(start_lat), "is_start": True})
    for lead in leads:
        coords.append({"lng": lead["lng"], "lat": lead["lat"], "lead": lead})

    if len(coords) < 2:
        return None, "Need at least 2 points for a route"

    coord_str = ";".join(f"{c['lng']},{c['lat']}" for c in coords)
    source_param = "first" if start_lat is not None else "any"

    try:
        resp = requests.get(
            f"https://router.project-osrm.org/trip/v1/driving/{coord_str}",
            params={"source": source_param, "roundtrip": "false",
                    "geometries": "geojson", "overview": "full"},
            timeout=30,
        )
        resp.raise_for_status()
        osrm = resp.json()
    except requests.RequestException as e:
        return None, f"OSRM request failed: {e}"

    if osrm.get("code") != "Ok":
        return None, f"OSRM error: {osrm.get('message', 'unknown')}"

    trip = osrm["trips"][0]
    waypoint_order = [w["waypoint_index"] for w in osrm["waypoints"]]

    ordered_leads = []
    for idx in waypoint_order:
        c = coords[idx]
        if "lead" in c:
            ordered_leads.append(c["lead"])

    gmaps_waypoints = [f"{lead['lat']},{lead['lng']}" for lead in ordered_leads]
    if start_lat is not None:
        origin = f"{start_lat},{start_lng}"
    else:
        origin = gmaps_waypoints.pop(0)

    gmaps_url = f"https://www.google.com/maps/dir/{origin}"
    for wp in gmaps_waypoints:
        gmaps_url += f"/{wp}"

    return {
        "ordered_leads": ordered_leads,
        "total_distance_km": round(trip["distance"] / 1000, 1),
        "total_duration_min": round(trip["duration"] / 60),
        "route_geometry": trip["geometry"],
        "google_maps_url": gmaps_url,
    }, None


@app.route("/api/trips", methods=["GET"])
def list_trips():
    db = get_db()
    rows = db.execute("""
        SELECT t.*, COUNT(tl.lead_id) as lead_count
        FROM trips t
        LEFT JOIN trip_leads tl ON tl.trip_id = t.id
        GROUP BY t.id
        ORDER BY t.created_at DESC
    """).fetchall()
    return jsonify([dict(r) for r in rows])


@app.route("/api/trips", methods=["POST"])
def create_trip():
    data = request.json or {}
    name = (data.get("name") or "").strip()
    if not name:
        return jsonify({"error": "Name required"}), 400
    lead_ids = data.get("lead_ids") or []
    db = get_db()
    cur = db.execute(
        "INSERT INTO trips (name, start_lat, start_lng, start_label, notes) "
        "VALUES (?, ?, ?, ?, ?)",
        (name, data.get("start_lat"), data.get("start_lng"),
         data.get("start_label", ""), data.get("notes", "")),
    )
    trip_id = cur.lastrowid
    for lid in lead_ids:
        db.execute(
            "INSERT OR IGNORE INTO trip_leads (trip_id, lead_id) VALUES (?, ?)",
            (trip_id, int(lid)),
        )
    db.commit()
    return jsonify({"id": trip_id})


@app.route("/api/trips/<int:trip_id>", methods=["GET"])
def get_trip(trip_id):
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        return jsonify({"error": "Not found"}), 404
    leads = db.execute("""
        SELECT l.* FROM leads l
        INNER JOIN trip_leads tl ON tl.lead_id = l.id
        WHERE tl.trip_id = ?
    """, (trip_id,)).fetchall()
    result = dict(trip)
    result["leads"] = [dict(r) for r in leads]
    return jsonify(result)


@app.route("/api/trips/<int:trip_id>", methods=["PATCH"])
def update_trip(trip_id):
    data = request.json or {}
    db = get_db()
    if not db.execute("SELECT id FROM trips WHERE id = ?", (trip_id,)).fetchone():
        return jsonify({"error": "Not found"}), 404

    fields, values = [], []
    for key in ("name", "start_lat", "start_lng", "start_label", "notes"):
        if key in data:
            fields.append(f"{key} = ?")
            values.append(data[key])
    if fields:
        values.append(trip_id)
        db.execute(f"UPDATE trips SET {', '.join(fields)} WHERE id = ?", values)

    if "lead_ids" in data:
        db.execute("DELETE FROM trip_leads WHERE trip_id = ?", (trip_id,))
        for lid in data["lead_ids"]:
            db.execute(
                "INSERT OR IGNORE INTO trip_leads (trip_id, lead_id) VALUES (?, ?)",
                (trip_id, int(lid)),
            )
    db.commit()
    return jsonify({"updated": True})


@app.route("/api/trips/<int:trip_id>", methods=["DELETE"])
def delete_trip(trip_id):
    db = get_db()
    db.execute("DELETE FROM trip_leads WHERE trip_id = ?", (trip_id,))
    db.execute("DELETE FROM trips WHERE id = ?", (trip_id,))
    db.commit()
    return jsonify({"deleted": True})


@app.route("/api/trips/<int:trip_id>/optimize", methods=["POST"])
def optimize_trip(trip_id):
    db = get_db()
    trip = db.execute("SELECT * FROM trips WHERE id = ?", (trip_id,)).fetchone()
    if not trip:
        return jsonify({"error": "Not found"}), 404

    rows = db.execute("""
        SELECT l.* FROM leads l
        INNER JOIN trip_leads tl ON tl.lead_id = l.id
        WHERE tl.trip_id = ? AND l.lat IS NOT NULL AND l.lng IS NOT NULL
    """, (trip_id,)).fetchall()
    leads = [dict(r) for r in rows]

    if not leads:
        return jsonify({"error": "No leads with coordinates in this trip"}), 400

    result, err = _osrm_optimize(leads, trip["start_lat"], trip["start_lng"])
    if err:
        return jsonify({"error": err}), 500
    return jsonify(result)


if __name__ == "__main__":
    app.run(debug=True, port=5001, host="0.0.0.0")
