# BRAIN.md - how Claude works with Clip Studio (Mac edits, Render approves)

The Mac does all the heavy lifting (fast). Render only shows finished clips so the
user can watch, approve/reject and download from any device.

Credentials: `.clipper_env` (CLIP_URL, CLIP_TOKEN). Never print or commit them.
Render free plan: first request after idle takes ~60 s, and files vanish on restart.

## Procedure
1. Get the long video file (the user's own, or with permission) on this Mac.
   Use the project venv: `.venv/bin/python`. ffmpeg comes from `imageio_ffmpeg`.
2. Find the moments: make frame sheets locally
   (`app.clipper.contact_sheet(src, out.jpg, start, end, cols, rows)`) and look at them;
   `app.clipper.audio_profile(src)` gives loudness. Zoom in on promising spots (60-80 s sheets).
   Pick 3-6 non-overlapping clips of 20-59 s with a hook in the first 3 s and a payoff at the end.
3. Write `clips_out/plan.json`:
   `[{"file":"1_name","start":722,"length":38,"layout":"stack","title":"...","description":"...","reason":"..."}]`
   layout: `stack` (game on top + facecam below; set CAM_BOX="x,y,w,h" fractions if the cam is elsewhere),
   `fit` (whole frame on blur), `crop` (center crop).
4. Cut: `PYTHONPATH=. .venv/bin/python local_cut.py "<video>" clips_out/plan.json clips_out`
5. Send to the dashboard: `.venv/bin/python push_clips.py "<video title>" clips_out/plan.json clips_out`
6. The user reviews at CLIP_URL, approves/rejects, edits titles, and downloads there.

## Rules
- Only process videos the user owns or has permission to use.
- Claude never posts publicly; approval and posting are the user's.
- Tell the user if the source is low resolution (soft output) - 1080p originals look far better.
- Report back: which clips, timestamps, and why each was picked.
