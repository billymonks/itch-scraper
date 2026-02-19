# itch.io Scraper

A lightweight web app that archives public projects from any itch.io creator, including metadata, cover art, and screenshots, into a downloadable ZIP.

## Quick Start (Docker)

```bash
docker compose up --build
```

Then open **http://localhost:8000** in your browser.

## Quick Start (Local)

```bash
pip install -r requirements.txt
uvicorn app:app --reload
```

## How It Works

1. Enter an itch.io creator username (e.g. `mossfield`).
2. Click **Scrape**.
3. The app crawls the creator's public project pages, collecting:
   - Title, description, tags, price, platform info, ratings
   - Cover / capsule artwork
   - Screenshots
4. Everything is packaged into a ZIP with a JSON index and per-project metadata files.
5. Download the ZIP when the progress bar completes.

## Project Structure

```
app.py              # FastAPI backend & API routes
scraper.py          # itch.io scraping logic
static/index.html   # Single-page frontend
Dockerfile
docker-compose.yml
requirements.txt
```
