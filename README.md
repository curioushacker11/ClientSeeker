# ClientSeeker

Client prospecting tool — paste social media links, find business locations on Google Maps, plan visit routes.

## Features

- Paste TikTok/Instagram/Facebook URLs to find businesses on Google Maps
- Bulk upload multiple links at once
- Track leads: To Visit, To Message, Contacted, Client, Skip
- Zone classification (Costa Rica regions)
- Route optimizer using OSRM (real road data)
- Trip planning with drag-to-reorder

## Requirements

- [Docker Desktop](https://www.docker.com/products/docker-desktop/)

## Setup

```bash
git clone https://github.com/YOUR_USERNAME/ClientSeeker.git
cd ClientSeeker
docker-compose up -d --build
```

First build takes a few minutes (downloads dependencies + headless browser).

## Usage

Open http://localhost:5001

- **Search tab**: Paste a social media URL, searches Google Maps for the business
- **Bulk tab**: Paste multiple URLs, processes them sequentially
- **Leads tab**: Manage your saved leads, update status, add notes
- **Route tab**: Plan optimized visit routes by zone
- **Trips tab**: Create trip plans, cherry-pick leads on a map

## How It Works

1. Paste a social media URL (TikTok, Instagram, Facebook)
2. Extracts the handle/username
3. Searches Google Maps via headless browser scraper
4. Found → saves as "To Visit" with address, phone, Maps link
5. Not found → saves as "To Message" (contact via social media)

## Services

| Service | Port | Description |
|---------|------|-------------|
| App | 5001 | Main dashboard |
| Scraper | 8001 | Google Maps scraper (internal) |

## Data

Your leads are stored in `instance/leads.db` (SQLite). This folder persists between container restarts.

## Stop/Start

```bash
docker-compose down    # Stop
docker-compose up -d   # Start
```

Or use Docker Desktop UI.
