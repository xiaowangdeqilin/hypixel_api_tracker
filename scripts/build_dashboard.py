import json
from datetime import datetime, timezone
from pathlib import Path

import requests

ENDPOINTS = {
    "collections": "https://api.hypixel.net/v2/resources/skyblock/collections",
    "skills": "https://api.hypixel.net/v2/resources/skyblock/skills",
    "items": "https://api.hypixel.net/v2/resources/skyblock/items",
    "bazaar": "https://api.hypixel.net/v2/skyblock/bazaar",
}


def fetch_json(url: str):
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def build_page(payload: dict, generated_at: str) -> str:
    rows = []
    for name, data in payload["endpoints"].items():
        rows.append(
            f"""
            <tr>
              <td>{name}</td>
              <td>{generated_at}</td>
              <td>{data.get('success', False)}</td>
              <td>{len(json.dumps(data)) if isinstance(data, (dict, list)) else str(data)}</td>
            </tr>
            """
        )

    return """<!DOCTYPE html>
<html lang="en">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1.0" />
    <title>Hypixel API Tracker</title>
    <style>
      body {
        font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
        background: #f5f7fb;
        color: #1f2937;
        margin: 0;
        padding: 24px;
      }
      .container {
        max-width: 1100px;
        margin: 0 auto;
      }
      h1 {
        margin-bottom: 8px;
      }
      .subtitle {
        color: #6b7280;
        margin-bottom: 24px;
      }
      .grid {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
        gap: 16px;
        margin-bottom: 24px;
      }
      .card {
        background: white;
        border-radius: 12px;
        padding: 18px;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
      }
      .label {
        color: #6b7280;
        font-size: 12px;
        text-transform: uppercase;
      }
      .value {
        margin-top: 8px;
        font-size: 20px;
        font-weight: 700;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        background: white;
        border-radius: 12px;
        overflow: hidden;
        box-shadow: 0 2px 10px rgba(0, 0, 0, 0.05);
      }
      th, td {
        text-align: left;
        padding: 12px 16px;
        border-bottom: 1px solid #e5e7eb;
      }
      th {
        background: #f9fafb;
      }
      code {
        font-family: SFMono-Regular, Consolas, monospace;
        background: #f3f4f6;
        padding: 2px 4px;
        border-radius: 4px;
      }
    </style>
  </head>
  <body>
    <div class="container">
      <h1>Hypixel API Tracker</h1>
      <div class="subtitle">Generated at {generated_at}</div>

      <div class="grid">
        <div class="card">
          <div class="label">Collections</div>
          <div class="value">{payload['endpoints'].get('collections', {}).get('status', 'unknown')}</div>
        </div>
        <div class="card">
          <div class="label">Skills</div>
          <div class="value">{payload['endpoints'].get('skills', {}).get('status', 'unknown')}</div>
        </div>
        <div class="card">
          <div class="label">Items</div>
          <div class="value">{payload['endpoints'].get('items', {}).get('status', 'unknown')}</div>
        </div>
        <div class="card">
          <div class="label">Bazaar</div>
          <div class="value">{payload['endpoints'].get('bazaar', {}).get('status', 'unknown')}</div>
        </div>
      </div>

      <table>
        <thead>
          <tr>
            <th>Endpoint</th>
            <th>Last Checked</th>
            <th>Success</th>
            <th>Payload Size</th>
          </tr>
        </thead>
        <tbody>
          {''.join(rows)}
        </tbody>
      </table>
    </div>
  </body>
</html>
"""


def main() -> None:
    site_dir = Path("site")
    site_dir.mkdir(exist_ok=True)

    fetched = {}
    for name, url in ENDPOINTS.items():
        try:
            data = fetch_json(url)
            fetched[name] = {
                "status": "ok",
                "success": True,
                "payload": data,
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }
        except Exception as exc:
            fetched[name] = {
                "status": "error",
                "success": False,
                "payload": {"error": str(exc)},
                "checked_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
            }

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC"),
        "endpoints": fetched,
    }

    (site_dir / "data.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    (site_dir / "index.html").write_text(build_page(payload, payload["generated_at"]), encoding="utf-8")
    (site_dir / "styles.css").write_text("body{font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;background:#f5f7fb;color:#1f2937;margin:0;padding:24px}a{color:#2563eb}", encoding="utf-8")


if __name__ == "__main__":
    main()
