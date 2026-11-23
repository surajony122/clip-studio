"""Web dashboard: upload a video, review the auto-cut Shorts, approve or reject."""
from __future__ import annotations

import hmac
import os
import queue
import re
import shutil
import threading
import time
import uuid
from typing import Optional

from fastapi import Depends, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from itsdangerous import BadSignature, URLSafeTimedSerializer

from . import clipper, db, download

BASE_DIR = os.path.dirname(os.path.dirname(__file__))
DATA_DIR = db.DATA_DIR
UPLOAD_DIR = os.path.join(DATA_DIR, "uploads")
CLIPS_DIR = os.path.join(DATA_DIR, "clips")
MAX_UPLOAD_MB = int(os.environ.get("MAX_UPLOAD_MB", "500"))
KEEP_SOURCE = os.environ.get("KEEP_SOURCE", "0") == "1"
SESSION_DAYS = 30

APP_PASSWORD = os.environ.get("APP_PASSWORD", "")
if not APP_PASSWORD:
    raise RuntimeError(
        "APP_PASSWORD is not set. Set it as an environment variable "
        "(on Render: Environment tab) before starting the app."
    )
SECRET_KEY = os.environ.get("SECRET_KEY") or ("clipper-" + APP_PASSWORD)
COOKIE_SECURE = os.environ.get("COOKIE_SECURE", "1") == "1"
signer = URLSafeTimedSerializer(SECRET_KEY, salt="session")

app = FastAPI(title="Clip Studio")
templates = Jinja2Templates(directory=os.path.join(BASE_DIR, "templates"))


# ---------------------------------------------------------------- auth
class NotAuthed(Exception):
    pass


@app.exception_handler(NotAuthed)
async def _not_authed(request: Request, exc: NotAuthed):
    return RedirectResponse("/login", status_code=303)


def require_login(request: Request) -> None:
    token = request.cookies.get("session")
    if not token:
        raise NotAuthed()
    try:
        signer.loads(token, max_age=SESSION_DAYS * 86400)
    except BadSignature:
        raise NotAuthed()


@app.get("/login")
def login_page(request: Request):
    return templates.TemplateResponse(request, "login.html", {"error": None})


@app.post("/login")
def login(request: Request, password: str = Form(...)):
    if not hmac.compare_digest(password.encode(), APP_PASSWORD.encode()):
        time.sleep(1)  # slow down password guessing
        return templates.TemplateResponse(
            request, "login.html", {"error": "Wrong password"}, status_code=401
        )
    resp = RedirectResponse("/", status_code=303)
    resp.set_cookie(
        "session", signer.dumps("ok"), max_age=SESSION_DAYS * 86400,
        httponly=True, samesite="lax", secure=COOKIE_SECURE,
    )
    return resp


@app.post("/logout")
def logout():
    resp = RedirectResponse("/login", status_code=303)
    resp.delete_cookie("session")
    return resp


@app.get("/healthz")
def healthz():
    return {"ok": True}


# ---------------------------------------------------------------- background worker
jobs: "queue.Queue[tuple[int, str, bool]]" = queue.Queue()
PLACEHOLDER_TITLE = "Video from link"


def _process(video_id: int, src: str, is_url: bool = False) -> None:
    video = db.one("SELECT * FROM videos WHERE id = ?", (video_id,))
    if not video:
        return
    out_dir = os.path.join(CLIPS_DIR, str(video_id))
    try:
        if is_url:
            db.execute("UPDATE videos SET status='downloading', error=NULL WHERE id=?", (video_id,))
            src, fetched_title = download.fetch_video(src, UPLOAD_DIR, MAX_UPLOAD_MB)
            if video["title"] == PLACEHOLDER_TITLE:
                video["title"] = fetched_title
                db.execute("UPDATE videos SET title=? WHERE id=?", (fetched_title, video_id))
        db.execute("UPDATE videos SET status='processing', error=NULL WHERE id=?", (video_id,))
        results = clipper.make_clips(
            src, out_dir, clip_len=video["clip_len"],
            max_clips=video["max_clips"], mode=video["mode"],
        )
        for i, r in enumerate(results, start=1):
            db.execute(
                "INSERT INTO clips (video_id,start,length,score,path,thumb,title,description) "
                "VALUES (?,?,?,?,?,?,?,?)",
                (video_id, r["start"], r["length"], r["score"], r["path"], r["thumb"],
                 f"{video['title']} - best moment {i} #Shorts",
                 f"Best moment {i} from {video['title']}\n\n#Shorts #gaming"),
            )
        db.execute("UPDATE videos SET status='ready' WHERE id=?", (video_id,))
    except Exception as exc:  # show the reason in the dashboard
        db.execute("UPDATE videos SET status='failed', error=? WHERE id=?", (str(exc)[:500], video_id))
    finally:
        if not is_url or not src.startswith("http"):  # src is a local file here
            if not KEEP_SOURCE and os.path.exists(src):
                os.remove(src)


def _worker() -> None:
    while True:
        video_id, src, is_url = jobs.get()
        try:
            _process(video_id, src, is_url)
        finally:
            jobs.task_done()


@app.on_event("startup")
def _startup() -> None:
    os.makedirs(UPLOAD_DIR, exist_ok=True)
    os.makedirs(CLIPS_DIR, exist_ok=True)
    # Anything still "processing" was cut off by a restart (common on free hosting).
    db.execute(
        "UPDATE videos SET status='failed', error='Server restarted while processing - upload again' "
        "WHERE status IN ('queued','downloading','processing')"
    )
    threading.Thread(target=_worker, daemon=True).start()


# ---------------------------------------------------------------- pages
@app.get("/")
def index(request: Request, _: None = Depends(require_login)):
    videos = db.query(
        "SELECT v.*, "
        "(SELECT COUNT(*) FROM clips c WHERE c.video_id=v.id) AS n_clips, "
        "(SELECT COUNT(*) FROM clips c WHERE c.video_id=v.id AND c.status='approved') AS n_approved "
        "FROM videos v ORDER BY v.id DESC"
    )
    return templates.TemplateResponse(
        request, "index.html", {"videos": videos, "max_mb": MAX_UPLOAD_MB, "max_min": download.MAX_MINUTES}
    )


@app.post("/upload")
async def upload(
    file: Optional[UploadFile] = File(None),
    url: str = Form(""),
    confirm_rights: Optional[str] = Form(None),
    title: str = Form(""),
    clip_len: int = Form(30),
    max_clips: int = Form(5),
    mode: str = Form("fit"),
    _: None = Depends(require_login),
):
    if not confirm_rights:
        raise HTTPException(400, "Please confirm you own this video or have the owner's permission.")
    url = url.strip()
    has_file = file is not None and bool(file.filename)
    if has_file == bool(url):
        raise HTTPException(400, "Give either a video link or a video file (not both, not neither).")
    clip_len = max(10, min(clip_len, 59))
    max_clips = max(1, min(max_clips, 10))
    if mode not in ("fit", "crop"):
        mode = "fit"

    if url:
        if download.classify(url) is None:
            raise HTTPException(400, "That doesn't look like a valid web link.")
        video_id = db.execute(
            "INSERT INTO videos (title, status, clip_len, max_clips, mode) VALUES (?,?,?,?,?)",
            (title.strip()[:80] or PLACEHOLDER_TITLE, "queued", clip_len, max_clips, mode),
        )
        jobs.put((video_id, url, True))
        return RedirectResponse(f"/video/{video_id}", status_code=303)

    assert file is not None
    ext = os.path.splitext(file.filename or "")[1].lower()
    if not re.fullmatch(r"\.(mp4|mov|mkv|webm|avi|m4v)", ext):
        raise HTTPException(400, "Please upload an mp4, mov, mkv, webm, avi or m4v video.")
    name = (title.strip() or os.path.splitext(file.filename or "video")[0])[:80]

    os.makedirs(UPLOAD_DIR, exist_ok=True)
    src = os.path.join(UPLOAD_DIR, f"{uuid.uuid4().hex}{ext}")
    limit = MAX_UPLOAD_MB * 1024 * 1024
    size = 0
    with open(src, "wb") as out:
        while True:
            chunk = await file.read(1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if size > limit:
                out.close()
                os.remove(src)
                raise HTTPException(413, f"File is larger than {MAX_UPLOAD_MB} MB.")
            out.write(chunk)

    video_id = db.execute(
        "INSERT INTO videos (title, status, clip_len, max_clips, mode) VALUES (?,?,?,?,?)",
        (name, "queued", clip_len, max_clips, mode),
    )
    jobs.put((video_id, src, False))
    return RedirectResponse(f"/video/{video_id}", status_code=303)


@app.get("/video/{video_id}")
def video_page(request: Request, video_id: int, _: None = Depends(require_login)):
    video = db.one("SELECT * FROM videos WHERE id = ?", (video_id,))
    if not video:
        raise HTTPException(404)
    clips = db.query("SELECT * FROM clips WHERE video_id = ? ORDER BY start", (video_id,))
    return templates.TemplateResponse(request, "video.html", {"video": video, "clips": clips})


@app.post("/clip/{clip_id}")
def update_clip(
    clip_id: int,
    action: str = Form(...),
    title: str = Form(""),
    description: str = Form(""),
    _: None = Depends(require_login),
):
    clip = db.one("SELECT * FROM clips WHERE id = ?", (clip_id,))
    if not clip:
        raise HTTPException(404)
    if clip["status"] == "posted":
        raise HTTPException(400, "Already posted.")
    status = {"approve": "approved", "reject": "rejected", "reset": "pending", "save": clip["status"]}.get(action)
    if status is None:
        raise HTTPException(400, "Unknown action.")
    db.execute(
        "UPDATE clips SET title=?, description=?, status=? WHERE id=?",
        (title.strip()[:100] or clip["title"], description.strip()[:4500], status, clip_id),
    )
    return RedirectResponse(f"/video/{clip['video_id']}#clip{clip_id}", status_code=303)


@app.post("/video/{video_id}/delete")
def delete_video(video_id: int, _: None = Depends(require_login)):
    db.execute("DELETE FROM videos WHERE id = ?", (video_id,))
    shutil.rmtree(os.path.join(CLIPS_DIR, str(video_id)), ignore_errors=True)
    return RedirectResponse("/", status_code=303)


@app.get("/media/{clip_id}/{kind}")
def media(clip_id: int, kind: str, _: None = Depends(require_login)):
    clip = db.one("SELECT path, thumb FROM clips WHERE id = ?", (clip_id,))
    if not clip or kind not in ("video", "thumb"):
        raise HTTPException(404)
    path = clip["path"] if kind == "video" else clip["thumb"]
    if not path or not os.path.exists(path):
        raise HTTPException(404, "File is gone (the server may have restarted).")
    return FileResponse(path, media_type="video/mp4" if kind == "video" else "image/jpeg")
