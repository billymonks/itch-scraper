from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from urllib.parse import urljoin

import httpx
from bs4 import BeautifulSoup, Tag

logger = logging.getLogger(__name__)

ITCH_TIMEOUT = httpx.Timeout(30.0, connect=10.0)
MAX_CONCURRENT_DOWNLOADS = 6


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    resp = await client.get(url, follow_redirects=True)
    resp.raise_for_status()
    return resp


async def _download_file(client: httpx.AsyncClient, url: str, dest: Path) -> bool:
    try:
        resp = await client.get(url, follow_redirects=True)
        resp.raise_for_status()
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(resp.content)
        return True
    except Exception:
        logger.warning("Failed to download %s", url, exc_info=True)
        return False


def _ext_from_url(url: str) -> str:
    path = url.split("?")[0]
    if "." in path.split("/")[-1]:
        return "." + path.split("/")[-1].rsplit(".", 1)[-1]
    return ".jpg"


def _clean(text: str | None) -> str:
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


# ── Creator page parsing ────────────────────────────────────────────

async def fetch_project_urls(client: httpx.AsyncClient, creator: str) -> list[str]:
    """Return all public project URLs for a creator, handling pagination."""
    base = f"https://{creator}.itch.io"
    urls: list[str] = []
    page = 1

    while True:
        page_url = base if page == 1 else f"{base}?page={page}"
        resp = await _fetch(client, page_url)
        soup = BeautifulSoup(resp.text, "lxml")

        cells = soup.select(".game_cell a.game_link, .game_cell a.title")
        if not cells:
            # also try the thumb link
            cells = soup.select(".game_cell .game_thumb a")

        found = 0
        for a in cells:
            href = a.get("href")
            if href and href not in urls:
                urls.append(str(href))
                found += 1

        next_btn = soup.select_one("a.next_page")
        if not next_btn or found == 0:
            break
        page += 1

    return urls


# ── Single project scraping ─────────────────────────────────────────

async def scrape_project(
    client: httpx.AsyncClient,
    url: str,
    sem: asyncio.Semaphore,
    base_dir: Path,
    on_progress=None,
) -> dict:
    async with sem:
        resp = await _fetch(client, url)

    soup = BeautifulSoup(resp.text, "lxml")

    title = _clean(soup.select_one("h1.game_title")
                   and soup.select_one("h1.game_title").get_text())
    if not title:
        og = soup.select_one('meta[property="og:title"]')
        title = og["content"] if og else url.rsplit("/", 1)[-1]

    slug = url.rstrip("/").rsplit("/", 1)[-1]
    project_dir = base_dir / slug
    project_dir.mkdir(parents=True, exist_ok=True)

    description = ""
    desc_el = soup.select_one(".formatted_description")
    if desc_el:
        description = _clean(desc_el.get_text())

    short_text = ""
    st_el = soup.select_one('meta[property="og:description"]')
    if st_el:
        short_text = _clean(str(st_el.get("content", "")))

    # Tags / classification
    tags: list[str] = []
    for tag_a in soup.select(".game_info_panel_widget a[href*='/tag-']"):
        tags.append(_clean(tag_a.get_text()))

    # Info panel key-value pairs
    info: dict[str, str] = {}
    info_table = soup.select(".game_info_panel_widget table tr")
    for row in info_table:
        cells = row.select("td")
        if len(cells) == 2:
            key = _clean(cells[0].get_text()).rstrip(":")
            val = _clean(cells[1].get_text())
            if key:
                info[key] = val

    # Price
    buy_btn = soup.select_one(".buy_btn_widget .price")
    price = _clean(buy_btn.get_text()) if buy_btn else "Free"

    # Platforms
    platforms: list[str] = []
    for span in soup.select(".game_info_panel_widget .icon"):
        cls = span.get("class", [])
        for c in cls:
            if c.startswith("icon-"):
                platforms.append(c.replace("icon-", ""))

    # Rating
    rating_el = soup.select_one('meta[itemprop="ratingValue"]')
    rating_count_el = soup.select_one('meta[itemprop="ratingCount"]')
    rating = rating_el["content"] if rating_el else None
    rating_count = rating_count_el["content"] if rating_count_el else None

    # ── Images ───────────────────────────────────────────────────────
    images_dir = project_dir / "images"
    images_dir.mkdir(exist_ok=True)
    download_tasks = []

    # Cover / capsule image
    cover_url = None
    cover_el = soup.select_one('meta[property="og:image"]')
    if cover_el:
        cover_url = str(cover_el["content"])
    if not cover_url:
        cover_el = soup.select_one(".header img, .game_cover img")
        if cover_el:
            cover_url = str(cover_el.get("src") or cover_el.get("data-lazy_src", ""))

    cover_saved = None
    if cover_url:
        ext = _ext_from_url(cover_url)
        cover_dest = images_dir / f"cover{ext}"
        download_tasks.append(("cover", cover_url, cover_dest))
        cover_saved = f"images/cover{ext}"

    # Screenshots
    screenshot_urls: list[str] = []
    for a_tag in soup.select(".screenshot_list a"):
        href = a_tag.get("href")
        if href:
            screenshot_urls.append(str(href))
    if not screenshot_urls:
        for img in soup.select(".screenshot_list img"):
            src = img.get("src") or img.get("data-lazy_src")
            if src:
                screenshot_urls.append(str(src))

    screenshot_saved: list[str] = []
    for i, surl in enumerate(screenshot_urls):
        ext = _ext_from_url(surl)
        dest = images_dir / f"screenshot_{i}{ext}"
        download_tasks.append((f"screenshot_{i}", surl, dest))
        screenshot_saved.append(f"images/screenshot_{i}{ext}")

    # Download all images concurrently
    async with sem:
        for _, img_url, dest in download_tasks:
            await _download_file(client, img_url, dest)

    metadata = {
        "url": url,
        "title": title,
        "slug": slug,
        "short_description": short_text,
        "description": description,
        "tags": tags,
        "info": info,
        "price": price,
        "platforms": platforms,
        "rating": rating,
        "rating_count": rating_count,
        "cover_image": cover_saved,
        "screenshots": screenshot_saved,
    }

    (project_dir / "metadata.json").write_text(
        json.dumps(metadata, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    if on_progress:
        on_progress(title or slug)

    return metadata


# ── Top-level orchestrator ───────────────────────────────────────────

async def scrape_creator(
    creator: str,
    output_dir: Path,
    on_progress=None,
) -> list[dict]:
    output_dir.mkdir(parents=True, exist_ok=True)
    sem = asyncio.Semaphore(MAX_CONCURRENT_DOWNLOADS)

    async with httpx.AsyncClient(timeout=ITCH_TIMEOUT, http2=False) as client:
        project_urls = await fetch_project_urls(client, creator)
        if not project_urls:
            raise ValueError(f"No public projects found for '{creator}'")

        if on_progress:
            on_progress(f"__total__{len(project_urls)}")

        results = []
        for url in project_urls:
            meta = await scrape_project(client, url, sem, output_dir, on_progress)
            results.append(meta)

    # Write a summary index
    (output_dir / "index.json").write_text(
        json.dumps(
            {"creator": creator, "project_count": len(results), "projects": results},
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return results
