"""Send clips edited on this computer to the Clip Studio dashboard for approval.

Usage: python push_clips.py "<video title>" clips_out/plan.json [clips_dir]
Reads CLIP_URL and CLIP_TOKEN from the environment or from .clipper_env.
"""
import json
import os
import subprocess
import sys

here = os.path.dirname(os.path.abspath(__file__))
env = dict(os.environ)
env_file = os.path.join(here, ".clipper_env")
if os.path.exists(env_file):
    for line in open(env_file):
        if "=" in line and not line.startswith("#"):
            k, v = line.strip().split("=", 1)
            env.setdefault(k, v)
url, token = env["CLIP_URL"].rstrip("/"), env["CLIP_TOKEN"]

video_title, plan_path = sys.argv[1], sys.argv[2]
clips_dir = sys.argv[3] if len(sys.argv) > 3 else os.path.dirname(plan_path) or "."

for c in json.load(open(plan_path)):
    cmd = [
        "curl", "-sS", "-m", "900", "-H", f"Authorization: Bearer {token}",
        "-F", f"file=@{os.path.join(clips_dir, c['file'])}.mp4",
        "-F", f"thumb=@{os.path.join(clips_dir, c['file'])}.jpg",
        "--form-string", f"video_title={video_title}",
        "--form-string", f"title={c['title']}",
        "--form-string", f"description={c.get('description', '')}",
        "--form-string", f"reason={c.get('reason', '')}",
        "--form-string", f"start={c['start']}",
        "--form-string", f"length={c['length']}",
        "-w", "\n  http %{http_code} in %{time_total}s\n", f"{url}/api/clips/upload",
    ]
    print(c["file"], "->", end=" ", flush=True)
    subprocess.run(cmd, check=True)
