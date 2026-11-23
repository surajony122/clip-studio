"""Find the most exciting moments in a video and cut them into vertical Shorts.

Light enough for a small server: audio loudness is streamed through ffmpeg
(no big files in memory) and no ML model is needed.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Dict, List, Optional

import numpy as np

SAMPLE_RATE = 8000  # Hz, mono 16-bit -> 16000 bytes per second of audio


def ffmpeg_bin() -> str:
    env = os.environ.get("FFMPEG_BIN")
    if env:
        return env
    found = shutil.which("ffmpeg")
    if found:
        return found
    import imageio_ffmpeg  # bundled binary, used for local development

    return imageio_ffmpeg.get_ffmpeg_exe()


def video_duration(path: str) -> float:
    """Duration in seconds, read from ffmpeg's banner output."""
    proc = subprocess.run(
        [ffmpeg_bin(), "-hide_banner", "-i", path],
        stderr=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        text=True,
    )
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", proc.stderr)
    if not m:
        raise ValueError("Could not read video duration - is this a valid video file?")
    h, mnt, s = m.groups()
    return int(h) * 3600 + int(mnt) * 60 + float(s)


def loudness_per_second(path: str) -> np.ndarray:
    """RMS loudness for each second of audio, streamed in small chunks."""
    cmd = [
        ffmpeg_bin(), "-v", "error", "-i", path, "-vn",
        "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-",
    ]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    bytes_per_sec = SAMPLE_RATE * 2
    values: List[float] = []
    buf = b""
    assert proc.stdout is not None
    while True:
        chunk = proc.stdout.read(bytes_per_sec * 30)
        if not chunk:
            break
        buf += chunk
        whole = (len(buf) // bytes_per_sec) * bytes_per_sec
        if whole:
            samples = np.frombuffer(buf[:whole], dtype=np.int16).astype(np.float32)
            samples = samples.reshape(-1, SAMPLE_RATE)
            values.extend(np.sqrt((samples ** 2).mean(axis=1)).tolist())
            buf = buf[whole:]
    if len(buf) >= bytes_per_sec // 2:
        samples = np.frombuffer(buf[: (len(buf) // 2) * 2], dtype=np.int16).astype(np.float32)
        values.append(float(np.sqrt((samples ** 2).mean())))
    proc.wait()
    return np.asarray(values, dtype=np.float32)


def pick_highlights(
    loudness: np.ndarray,
    clip_len: int = 30,
    max_clips: int = 5,
    min_gap: int = 10,
) -> List[Dict[str, float]]:
    """Choose the loudest, spikiest non-overlapping windows.

    Score = how far a window sits above the video's usual loudness, plus a
    bonus for its single biggest burst (shouting, explosions, big reactions).
    """
    n = len(loudness)
    if n == 0:
        return []
    clip_len = max(5, min(clip_len, 59))
    if n <= clip_len:
        return [{"start": 0.0, "length": float(n), "score": float(loudness.mean())}]

    baseline = float(np.median(loudness)) + 1e-3
    rel = loudness / baseline
    csum = np.concatenate([[0.0], np.cumsum(rel)])
    starts = np.arange(0, n - clip_len + 1)
    means = (csum[starts + clip_len] - csum[starts]) / clip_len
    peaks = np.array([rel[s : s + clip_len].max() for s in starts])
    scores = means + 0.3 * peaks

    chosen: List[Dict[str, float]] = []
    taken = np.zeros(len(starts), dtype=bool)
    for idx in np.argsort(-scores):
        if len(chosen) >= max_clips:
            break
        if taken[idx]:
            continue
        s = int(starts[idx])
        chosen.append({"start": float(s), "length": float(clip_len), "score": float(scores[idx])})
        lo = max(0, idx - clip_len - min_gap)
        hi = min(len(starts), idx + clip_len + min_gap + 1)
        taken[lo:hi] = True
    # A score near 1.0 means "just average loudness": not worth posting.
    # Always keep the best one so a flat video still gives something to review.
    chosen = [c for i, c in enumerate(chosen) if i == 0 or c["score"] >= 1.5]
    chosen.sort(key=lambda c: c["start"])
    return chosen


def _video_filter(mode: str) -> List[str]:
    if mode == "crop":
        vf = (
            "crop='if(gte(iw/ih,9/16),ih*9/16,iw)':'if(gte(iw/ih,9/16),ih,iw*16/9)',"
            "scale=1080:1920,setsar=1,fps=30"
        )
        return ["-vf", vf]
    # "fit": whole game screen in the middle on a blurred copy of itself
    fc = (
        "[0:v]split=2[a][b];"
        "[a]scale=1080:1920:force_original_aspect_ratio=increase,crop=1080:1920,boxblur=25:5[bg];"
        "[b]scale=1080:-2[fg];"
        "[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1,fps=30[v]"
    )
    return ["-filter_complex", fc, "-map", "[v]", "-map", "0:a?"]


def cut_short(
    src: str,
    dest: str,
    start: float,
    length: float,
    mode: str = "fit",
    thumb: Optional[str] = None,
) -> None:
    """Cut one vertical 1080x1920 Short (H.264 + AAC) from the source video."""
    cmd = [
        ffmpeg_bin(), "-y", "-v", "error",
        "-ss", f"{start:.2f}", "-t", f"{length:.2f}", "-i", src,
        *_video_filter(mode),
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "23", "-pix_fmt", "yuv420p",
        "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart",
        dest,
    ]
    subprocess.run(cmd, check=True)
    if thumb:
        subprocess.run(
            [ffmpeg_bin(), "-y", "-v", "error", "-ss", "1", "-i", dest,
             "-frames:v", "1", "-vf", "scale=360:-2", thumb],
            check=True,
        )


def make_clips(
    src: str,
    out_dir: str,
    clip_len: int = 30,
    max_clips: int = 5,
    mode: str = "fit",
) -> List[Dict[str, object]]:
    """Full pipeline: score the video, pick highlights, cut each into a Short."""
    os.makedirs(out_dir, exist_ok=True)
    loud = loudness_per_second(src)
    if len(loud) == 0:
        # no audio track: fall back to evenly spaced windows
        total = int(video_duration(src))
        loud = np.ones(total, dtype=np.float32)
    picks = pick_highlights(loud, clip_len=clip_len, max_clips=max_clips)
    results: List[Dict[str, object]] = []
    for i, p in enumerate(picks, start=1):
        video_path = os.path.join(out_dir, f"clip_{i}.mp4")
        thumb_path = os.path.join(out_dir, f"clip_{i}.jpg")
        cut_short(src, video_path, p["start"], p["length"], mode=mode, thumb=thumb_path)
        results.append({**p, "path": video_path, "thumb": thumb_path})
    return results
