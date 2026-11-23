# BRAIN.md - how Claude operates Clip Studio

Claude is the editor's brain. The hosted tool holds the raw video and does the
ffmpeg work; Claude decides WHICH moments are worth posting and why.

Credentials: `.clipper_env` in this folder (CLIP_URL, CLIP_TOKEN). Never print
the token or commit it. Send it as `Authorization: Bearer $CLIP_TOKEN`.
The server sleeps on the free plan: the first call can take ~60 s (use `curl -m 120`).

## Procedure for each video with status "awaiting"
1. `GET /api/videos` -> find videos with status `awaiting`.
2. `GET /api/videos/{id}/profile?clip_len=30` -> loudness every 5 s (1.0 normal,
   3+ = big reaction) and the loudest candidate windows.
3. `GET /api/videos/{id}/sheet?start=0&end=DURATION&cols=4&rows=4` -> save the JPEG
   and look at it. 16 frames, left-to-right, top-to-bottom, evenly spaced.
   For long videos make several sheets (e.g. 10-minute chunks).
4. Zoom in around promising spots: same call with a narrow start/end (30-60 s).
5. Choose 3-6 clips. Good = a clear event (kill, clutch, fail, big reaction, funny
   moment) with a hook in the first 3 s, ends on the payoff. 20-45 s each (max 59 for
   Shorts). Don't overlap clips. Don't pick moments just because they are loud.
6. `POST /api/videos/{id}/clips` JSON: `{"start":..,"length":..,"title":"..","description":"..","reason":".."}`
   Title <= 100 chars, specific and curiosity-driven. `reason` is shown to the user.
7. `POST /api/videos/{id}/finish`. The user then approves/rejects in the dashboard.

## Rules
- Only process videos the user owns or has permission to use (the upload form requires a tick).
- Claude never posts anything publicly; approval is always the user's.
- If a call returns 410, the raw file was wiped by a restart: tell the user to re-upload.
- Report back: which clips, timestamps, and the reason for each.
