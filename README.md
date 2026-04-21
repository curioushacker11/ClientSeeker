# ClientSeeker

Client prospecting tool — paste social media links, find business locations on Google Maps, plan visit routes.

## Features

- Paste TikTok/Instagram/Facebook URLs to find businesses on Google Maps
- Bulk upload multiple links at once
- Track leads: To Visit, To Message, Contacted, Client, Skip
- Zone classification (Costa Rica regions)
- Route optimizer using OSRM (real road data)
- Trip planning with drag-to-reorder

## Setup (Windows)

### Step 1: Install Docker Desktop (one-time)
1. Download from https://www.docker.com/products/docker-desktop/
2. Run the installer, restart your computer if prompted
3. Open Docker Desktop and wait for it to fully start (whale icon in system tray turns steady)

### Step 2: Download this project
1. Click the green **Code** button above → **Download ZIP**
2. Extract the ZIP to your Desktop (or any folder)

### Step 3: Run the app
1. Open the extracted `ClientSeeker` folder
2. Click the address bar, type `powershell`, press Enter
3. Run this command:
```powershell
docker-compose up -d --build
```
4. Wait 3-5 minutes (first time only — downloads everything)

### Step 4: Open the app
Go to http://localhost:5001 in your browser

## Usage

- **Search tab**: Paste a social media URL, searches Google Maps for the business
- **Bulk tab**: Paste multiple URLs, processes them sequentially
- **Leads tab**: Manage your saved leads, update status, add notes
- **Route tab**: Plan optimized visit routes by zone
- **Trips tab**: Create trip plans, cherry-pick leads on a map

## How It Works

1. Paste a social media URL (TikTok, Instagram, Facebook)
2. Extracts the handle/username
3. Searches Google Maps via headless browser
4. Found → saves as "To Visit" with address, phone, Maps link
5. Not found → saves as "To Message" (contact via social media)

## Stop / Start / Restart

In PowerShell (from the ClientSeeker folder):
```powershell
docker-compose down      # Stop
docker-compose up -d     # Start
docker-compose restart   # Restart
```

Or just use Docker Desktop — click the stop/start buttons on the containers.

## Data

Your leads are saved in the `instance` folder. This persists even if you stop the containers.

## Troubleshooting

**"Docker is not running"** — Open Docker Desktop and wait for it to fully start

**Port 5001 already in use** — Stop other apps using that port, or edit `docker-compose.yml` to change `5001:5001` to another port like `5002:5001`

**First search is slow** — Normal, the scraper browser takes ~10 seconds to start up
