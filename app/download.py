"""Fetch a video from a link so it can be cut into Shorts.

Supported: Google Drive share links, Dropbox share links, direct file links
(.mp4 etc.), and - best effort only - YouTube links.

Only use this for videos you own or have the owner's permission to use.
YouTube's terms only allow downloads through its own features and it often
blocks servers such as Render; when that happens, use a Drive/Dropbox link or
upload the file instead. This module does not try to get around that block.
"""
from __future__ import annotations

import ipaddress
import os
import re
import socket
import urllib.error
import urllib.request
import uuid
from typing import Optional, Tuple
from urllib.parse import parse_qs, urlparse, urlunparse

from . import clipper

YOUTUBE_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be", "music.youtube.com"}
DRIVE_HOSTS = {"drive.google.com", "docs.google.com"}
DROPBOX_HOSTS = {"www.dropbox.com", "dropbox.com", "dl.dropboxusercontent.com"}
VIDEO_EXTS = (".mp4", ".mov", ".mkv", ".webm", ".avi", ".m4v")
MAX_MINUTES = int(os.environ.get("MAX_VIDEO_MINUTES", "60"))
ALLOW_PRIVATE = os.environ.get("ALLOW_PRIVATE_URLS", "0") == "1"  # local testing only


def _host(url: str) -> str:
    try:
        return (urlparse(url.strip()).hostname or "").lower()
    except ValueError:
        return ""


def classify(url: str) -> Optional[str]:
    """'youtube' | 'drive' | 'dropbox' | 'direct', or None if not a usable link."""
    try:
        parsed = urlparse(url.strip())
    except ValueError:
        return None
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        return None
    host = parsed.hostname.lower()
    if host in YOUTUBE_HOSTS:
        return "youtube"
    if host in DRIVE_HOSTS:
        return "drive"
    if host in DROPBOX_HOSTS:
        return "dropbox"
    return "direct"


def _check_public_host(host: str) -> None:
    """Refuse links that point at the server itself or its private network."""
    if ALLOW_PRIVATE:
        return
    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        raise ValueError("Could not find that website.")
    for info in infos:
        ip = ipaddress.ip_address(info[4][0])
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("That link points to a private address, which isn't allowed.")


class _SafeRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if classify(newurl) is None:
            raise ValueError("Link redirected somewhere that isn't allowed.")
        _check_public_host(_host(newurl))
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def _download_direct(url: str, dest_dir: str, max_mb: int) -> Tuple[str, str]:
    _check_public_host(_host(url))
    opener = urllib.request.build_opener(_SafeRedirect)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 ClipStudio"})
    limit = max_mb * 1024 * 1024
    try:
        resp = opener.open(req, timeout=30)
    except urllib.error.HTTPError as exc:
        raise ValueError(f"The link returned an error ({exc.code}). Is it shared publicly?")
    except (urllib.error.URLError, socket.timeout):
        raise ValueError("Could not reach that link.")

    with resp:
        ctype = (resp.headers.get("Content-Type") or "").lower()
        path_ext = os.path.splitext(urlparse(resp.geturl()).path)[1].lower()
        if path_ext in VIDEO_EXTS:
            ext = path_ext
        elif ctype.startswith("video/"):
            ext = ".mp4"
        else:
            raise ValueError("That link doesn't lead to a video file (got a web page instead).")
        declared = int(resp.headers.get("Content-Length") or 0)
        if declared > limit:
            raise ValueError(f"Video is larger than {max_mb} MB.")
        os.makedirs(dest_dir, exist_ok=True)
        dest = os.path.join(dest_dir, uuid.uuid4().hex + ext)
        size = 0
        try:
            with open(dest, "wb") as out:
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    size += len(chunk)
                    if size > limit:
                        raise ValueError(f"Video is larger than {max_mb} MB.")
                    out.write(chunk)
        except Exception:
            if os.path.exists(dest):
                os.remove(dest)
            raise
    name = os.path.splitext(os.path.basename(urlparse(url).path))[0] or "Video"
    return dest, name[:80]


def _dropbox_direct(url: str) -> str:
    p = urlparse(url)
    q = {k: v for k, v in parse_qs(p.query).items() if k != "dl"}
    query = "&".join(f"{k}={v[0]}" for k, v in q.items())
    query = (query + "&" if query else "") + "dl=1"
    return urlunparse(p._replace(query=query))


def _download_drive(url: str, dest_dir: str, max_mb: int) -> Tuple[str, str]:
    import gdown

    m = re.search(r"/d/([\w-]+)", url) or re.search(r"[?&]id=([\w-]+)", url)
    if not m:
        raise ValueError("Could not find the file in that Google Drive link.")
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, uuid.uuid4().hex + ".mp4")
    try:
        out = gdown.download(id=m.group(1), output=dest, quiet=True)
    except Exception as exc:
        raise ValueError("Google Drive refused the download. Set sharing to 'Anyone with the link'.") from exc
    if not out or not os.path.exists(out):
        raise ValueError("Google Drive refused the download. Set sharing to 'Anyone with the link'.")
    if os.path.getsize(out) > max_mb * 1024 * 1024:
        os.remove(out)
        raise ValueError(f"Video is larger than {max_mb} MB.")
    return out, "Drive video"


def _download_youtube(url: str, dest_dir: str, max_mb: int) -> Tuple[str, str]:
    import yt_dlp

    os.makedirs(dest_dir, exist_ok=True)
    base = os.path.join(dest_dir, uuid.uuid4().hex)
    opts = {
        "format": "bv*[height<=720]+ba/b[height<=720]/b",
        "outtmpl": base + ".%(ext)s",
        "merge_output_format": "mp4",
        "noplaylist": True,
        "quiet": True,
        "no_warnings": True,
        "ffmpeg_location": clipper.ffmpeg_bin(),
        "max_filesize": max_mb * 1024 * 1024,
        "match_filter": yt_dlp.utils.match_filter_func(f"duration <= {MAX_MINUTES * 60}"),
    }
    try:
        with yt_dlp.YoutubeDL(opts) as ydl:
            info = ydl.extract_info(url, download=True)
    except yt_dlp.utils.DownloadError as exc:
        msg = str(exc)
        if "private" in msg.lower() or "unavailable" in msg.lower():
            raise ValueError("That video is private or unavailable.")
        raise ValueError(
            "YouTube wouldn't let the server download this video. Share the original file "
            "through Google Drive or Dropbox instead, or upload it."
        )
    if not info:
        raise ValueError(f"Video skipped - it may be longer than {MAX_MINUTES} minutes.")
    downloads = info.get("requested_downloads") or []
    path = downloads[0].get("filepath") if downloads else None
    if not path or not os.path.exists(path):
        raise ValueError(f"Download failed or the file is larger than {max_mb} MB.")
    return path, (info.get("title") or "YouTube video")[:80]


def fetch_video(url: str, dest_dir: str, max_mb: int) -> Tuple[str, str]:
    """Return (local_file_path, title). Raises ValueError with a readable message."""
    kind = classify(url)
    if kind == "youtube":
        return _download_youtube(url, dest_dir, max_mb)
    if kind == "drive":
        return _download_drive(url, dest_dir, max_mb)
    if kind == "dropbox":
        return _download_direct(_dropbox_direct(url), dest_dir, max_mb)
    if kind == "direct":
        return _download_direct(url, dest_dir, max_mb)
    raise ValueError("That isn't a valid link.")
