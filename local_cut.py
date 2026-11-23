"""Cut Shorts locally (fast, on your own computer) from a long video.

Usage: python local_cut.py "<video.mp4>" plan.json [out_dir]
plan.json = [{"file": "1_name", "start": 722, "length": 38, "layout": "fit"}, ...]
"""
import json
import os
import sys
import time

from app import clipper

src, plan_path = sys.argv[1], sys.argv[2]
out_dir = sys.argv[3] if len(sys.argv) > 3 else "clips_out"
os.makedirs(out_dir, exist_ok=True)
for c in json.load(open(plan_path)):
    t = time.time()
    dest = os.path.join(out_dir, c["file"] + ".mp4")
    clipper.cut_short(src, dest, c["start"], c["length"], mode=c.get("layout", "fit"),
                      thumb=os.path.join(out_dir, c["file"] + ".jpg"))
    print(f'{c["file"]}: {os.path.getsize(dest)/1e6:.1f} MB in {time.time()-t:.0f}s')
