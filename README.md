# WareBot

Smart warehouse robot simulation built with Python, Flask, BFS/DFS/A* search, and a browser dashboard.

## Run locally

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:5000.

## Deployment

The Flask application is compatible with Vercel's Python runtime. Vercel serves the dashboard assets from `public/`; when running locally, Flask serves the same root URLs from `static/`.
