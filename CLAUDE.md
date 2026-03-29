# ClientSeeker

Client prospecting tool — paste social media links, find business locations on Google Maps, plan visit routes.

## Architecture

```
docker-compose.yml          # Runs both services
├── app (Flask, port 5001)  # Main web UI + API
│   ├── app.py              # Backend: search, leads CRUD, zones, route optimizer
│   ├── templates/index.html # Single-page frontend (vanilla JS)
│   ├── Dockerfile
│   └── instance/leads.db   # SQLite database (volume-mounted, persists)
└── scraper/                # Google Maps scraper (cloned from github)
    ├── docker-compose.yml  # Standalone compose (not used anymore)
    └── gmaps_scraper_server/ # FastAPI + Puppeteer headless browser
```

## How It Works

1. **Paste social media URL** (TikTok/Instagram/Facebook)
2. **Extract handle** from URL (exact, no transformation)
3. **Search Google Maps** via local scraper: `"{handle} Costa Rica"`
4. **Found** → save as "To Visit" with address, coords, phone, Maps link
5. **Not found** → save as "To Message" with profile link
6. **Classify zones** → reverse geocode coords via Nominatim → assign Central/Pérez Zeledón/Unclassified
7. **Route planner** → OSRM optimizes visit order → opens in Google Maps

## Key Features

- **Single search**: one link at a time, shows result with save options
- **Bulk upload**: paste list of links, processes sequentially, auto-saves
- **Leads management**: status tracking, notes, reviewed checkbox, edit name/maps/followers
- **Follower count**: auto-fetch for TikTok, manual edit for others
- **Zone system**: Central (Alajuela, Heredia, Cartago, San José minus excluded cantons), Pérez Zeledón, Unclassified
- **Route planner**: filter by zone, set starting point, OSRM optimized route, Google Maps link
- **Mobile friendly**: accessible on phone via local network, persists tab state across reloads

## Running

```bash
cd ~/Desktop/work_projects/ClientSeeker
docker-compose up -d --build     # Start both services
docker-compose down              # Stop
docker-compose logs -f           # View logs
```

- App: http://localhost:5001 (or http://192.168.1.19:5001 from phone on same WiFi)
- Scraper: http://localhost:8001 (internal, app calls it directly)
- Also controllable from Docker Desktop (stop/start buttons)

## Database

SQLite at `instance/leads.db`. Key columns:
- `platform`, `handle`, `profile_url` — social media source
- `business_name`, `address`, `phone`, `website`, `rating` — from Maps scraper
- `maps_url`, `lat`, `lng` — location data
- `status` — new/to_visit/to_message/contacted/client/skip
- `zone` — Central/Pérez Zeledón/Unclassified
- `followers` — follower count (auto-fetched for TikTok)
- `reviewed` — checkbox state
- `notes` — free text

## External Services (all free)

- **Google Maps scraper** (self-hosted Docker): headless browser, no API key, unlimited but ~5-10s per search
- **Nominatim** (OpenStreetMap): reverse geocoding for zone classification, 1 req/sec rate limit
- **OSRM** (public API): route optimization, uses real road data (one-way streets, etc.)

## Zone Definitions (Costa Rica)

- **Central**: provinces Alajuela, Heredia, Cartago + San José (excluding Pérez Zeledón, Puriscal, Tarrazú, Mora, Turrubares, León Cortés Castro)
- **Pérez Zeledón**: canton of Pérez Zeledón only
- **Unclassified**: everything else (Guanacaste, Puntarenas, Limón, excluded SJ cantons)

## Important Behaviors

- **Removing Maps link** clears lat/lng/zone too (wrong scraper match = wrong coords)
- **Changing status to "to message"** also clears Maps/coords/zone
- **Short Maps URLs** (maps.app.goo.gl) are resolved to full URLs when classifying zones
- **Scraper language**: Spanish (`lang: "es"`) for better Costa Rica results
- **Search region**: always appends "Costa Rica" to handle name
- Search uses **exact handle name** — no underscore/dot to space conversion
- `_clean()` strips HTML entities (`&nbsp;`) and review count text ("6 opiniones") from scraper output
