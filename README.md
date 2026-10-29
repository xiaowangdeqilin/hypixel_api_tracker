# Hypixel API Tracker

A small monitoring app for tracking changes in the Hypixel SkyBlock public APIs:

- https://api.hypixel.net/v2/resources/skyblock/collections
- https://api.hypixel.net/v2/resources/skyblock/skills
- https://api.hypixel.net/v2/resources/skyblock/items
- https://api.hypixel.net/v2/skyblock/bazaar

This project stores API snapshots, detects changes against the previous snapshot, and exposes a lightweight web dashboard.

## Features

- Scheduled polling for all 4 endpoints
- Snapshot storage in SQLite
- Change detection for added/removed/modified values
- Simple FastAPI dashboard
- Recent change logs and endpoint status summary

## Quick start

1. Install dependencies:

   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -r requirements.txt
   ```

2. Initialize the database:

   ```bash
   python -c "from app.db import init_db; init_db()"
   ```

3. Start the server:

   ```bash
   uvicorn app.main:app --host 0.0.0.0 --port 8000 --reload
   ```

4. Open:

   ```text
   http://localhost:8000/
   ```

## Configuration

Copy `.env.example` and set your Hypixel API key if needed.

## Notes

- Bazaar is polled frequently because it changes more often.
- Collections, skills, and items are polled less frequently because they are more static.
- Snapshots are saved locally in SQLite for lightweight historical tracking.

## Project structure

```text
app/
  __init__.py
  config.py
  db.py
  diff.py
  fetcher.py
  scheduler.py
  main.py
data/
  .gitkeep
templates/
  index.html
static/
  app.js
  styles.css
requirements.txt
.env.example
README.md
```
