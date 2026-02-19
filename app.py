from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from scraper import scrape_creator

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(title="itch.io Scraper")

WORK_DIR = Path(tempfile.gettempdir()) / "itch_scraper"
WORK_DIR.mkdir(exist_ok=True)

jobs: dict[str, dict] = {}


@app.get("/", response_class=HTMLResponse)
async def index():
    return (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")


@app.post("/api/scrape")
async def start_scrape(request: Request):
    body = await request.json()
    creator = body.get("creator", "").strip().lower()
    if not creator or not re.match(r"^[a-z0-9\-]+$", creator):
        raise HTTPException(400, "Invalid creator name. Use the itch.io username (letters, numbers, hyphens).")

    job_id = uuid.uuid4().hex[:12]
    jobs[job_id] = {"status": "running", "progress": [], "total": 0, "error": None, "zip": None}

    asyncio.create_task(_run_scrape(job_id, creator))
    return {"job_id": job_id}


@app.get("/api/status/{job_id}")
async def job_status(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return job


@app.get("/api/download/{job_id}")
async def download_zip(job_id: str):
    job = jobs.get(job_id)
    if not job or not job.get("zip"):
        raise HTTPException(404, "Zip not ready")
    zip_path = Path(job["zip"])
    if not zip_path.exists():
        raise HTTPException(404, "Zip file missing")
    return FileResponse(
        zip_path,
        media_type="application/zip",
        filename=zip_path.name,
    )


async def _run_scrape(job_id: str, creator: str):
    job = jobs[job_id]
    output_dir = WORK_DIR / job_id / creator

    def on_progress(msg: str):
        if msg.startswith("__total__"):
            job["total"] = int(msg.replace("__total__", ""))
        else:
            job["progress"].append(msg)

    try:
        await scrape_creator(creator, output_dir, on_progress=on_progress)

        zip_base = WORK_DIR / job_id / f"{creator}_itch"
        zip_path = shutil.make_archive(str(zip_base), "zip", root_dir=str(output_dir))

        job["zip"] = zip_path
        job["status"] = "done"
    except Exception as e:
        logger.exception("Scrape failed for %s", creator)
        job["status"] = "error"
        job["error"] = str(e)
