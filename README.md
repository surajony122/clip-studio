# Clip Studio

Upload a gaming video -> it finds the loudest/most exciting moments -> cuts vertical
1080x1920 Shorts -> you review and approve them from your phone.

Only use videos you own or have written permission to use.

## Run locally
    python3 -m venv .venv && .venv/bin/pip install -r requirements.txt -r requirements-dev.txt
    APP_PASSWORD=choose-a-password COOKIE_SECURE=0 .venv/bin/uvicorn app.main:app --port 8000

## Deploy on Render (free plan)
1. Push this folder to a GitHub repo.
2. Render -> New -> Blueprint -> pick the repo (it reads `render.yaml`).
3. When asked, set `APP_PASSWORD` (your dashboard password).
4. Open the `.onrender.com` URL and log in.

Free-plan limits: the service sleeps after ~15 min idle (first load takes ~1 min),
has 512 MB RAM, and the disk is wiped on every restart - so review and approve
clips soon after processing. A paid instance + persistent disk fixes all three.
