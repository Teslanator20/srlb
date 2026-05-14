"""
srlb — Seasonal Raid Leaderboard.

Reads a list of guild names from guilds.txt, polls
https://api.wynncraft.com/v3/guild/<name> every POLL_INTERVAL_S seconds, and
builds a leaderboard sorted by the current season's `seasonRanks[<latest>].rating`.
Serves an HTML page on http://localhost:PORT and a JSON API at /api/leaderboard.
"""

import asyncio
import json
import os
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
from aiohttp import web

POLL_INTERVAL_S = int(os.environ.get("POLL_INTERVAL_S", "120"))
PORT = int(os.environ.get("PORT", "8080"))
CONCURRENCY = int(os.environ.get("CONCURRENCY", "6"))

ROOT = Path(__file__).parent
STATE_PATH = ROOT / "state.json"
INDEX_PATH = ROOT / "index.html"
GUILDS_PATH = ROOT / "guilds.txt"

STATE = {"updated": None, "season": None, "guilds": [], "errors": []}


def load_guild_names() -> list[str]:
    if not GUILDS_PATH.exists():
        return []
    out = []
    seen = set()
    for line in GUILDS_PATH.read_text(encoding="utf-8").splitlines():
        name = line.strip()
        if not name or name.startswith("#"):
            continue
        key = name.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(name)
    return out


async def _get(session: aiohttp.ClientSession, url: str) -> tuple[int, dict | None]:
    async with session.get(url, timeout=aiohttp.ClientTimeout(total=20)) as r:
        if r.status != 200:
            return r.status, None
        return 200, await r.json()


async def fetch_guild(session: aiohttp.ClientSession, entry: str) -> tuple[str, dict | None, str | None]:
    """Resolve `entry` as either a guild name or a prefix. Tries name first, then prefix."""
    quoted = urllib.parse.quote(entry)
    try:
        status, data = await _get(session, f"https://api.wynncraft.com/v3/guild/{quoted}")
        if status == 200:
            return entry, data, None
        if status != 404:
            return entry, None, f"HTTP {status}"
        status, data = await _get(session, f"https://api.wynncraft.com/v3/guild/prefix/{quoted}")
        if status == 200:
            return entry, data, None
        if status == 404:
            return entry, None, "not found"
        return entry, None, f"HTTP {status}"
    except Exception as e:
        return entry, None, str(e)


def latest_season(season_ranks: dict) -> int | None:
    if not season_ranks:
        return None
    nums = []
    for k in season_ranks.keys():
        try:
            nums.append(int(k))
        except ValueError:
            pass
    return max(nums) if nums else None


def build_row(name: str, data: dict) -> dict | None:
    season_ranks = data.get("seasonRanks") or {}
    season = latest_season(season_ranks)
    entry = season_ranks.get(str(season)) if season is not None else None
    rating = int(entry.get("rating", 0)) if entry else 0
    final_terr = int(entry.get("finalTerritories", 0)) if entry else 0
    return {
        "uuid": data.get("uuid"),
        "name": data.get("name", name),
        "prefix": data.get("prefix", ""),
        "level": data.get("level"),
        "xpPercent": data.get("xpPercent"),
        "territories": data.get("territories", 0),
        "wars": data.get("wars", 0),
        "raids": data.get("raids", 0),
        "season": season,
        "rating": rating,
        "finalTerritories": final_terr,
        "banner": data.get("banner"),
    }


async def poll_once(session: aiohttp.ClientSession) -> None:
    names = load_guild_names()
    if not names:
        print("[poll] guilds.txt is empty")
        return

    sem = asyncio.Semaphore(CONCURRENCY)

    async def bounded(n: str):
        async with sem:
            return await fetch_guild(session, n)

    results = await asyncio.gather(*(bounded(n) for n in names))

    rows: list[dict] = []
    errors: list[dict] = []
    seasons_seen: dict[int, int] = {}
    seen_uuids: set[str] = set()

    for name, data, err in results:
        if err or data is None:
            errors.append({"name": name, "error": err or "no data"})
            continue
        row = build_row(name, data)
        if row["uuid"] in seen_uuids:
            continue
        seen_uuids.add(row["uuid"])
        if row["season"] is not None:
            seasons_seen[row["season"]] = seasons_seen.get(row["season"], 0) + 1
        rows.append(row)

    current_season = max(seasons_seen, key=seasons_seen.get) if seasons_seen else None

    for r in rows:
        r["isCurrentSeason"] = r["season"] == current_season

    rows.sort(key=lambda r: (-r["rating"], r["name"].lower()))
    for i, r in enumerate(rows, start=1):
        r["rank"] = i

    STATE["guilds"] = rows
    STATE["season"] = current_season
    STATE["errors"] = errors
    STATE["updated"] = datetime.now(timezone.utc).isoformat()
    STATE_PATH.write_text(json.dumps(STATE, indent=2))
    ts = datetime.now(timezone.utc).strftime("%H:%M:%S")
    print(f"[{ts}] season {current_season}: {len(rows)} guilds, {len(errors)} errors, top SR={rows[0]['rating'] if rows else 0}")


async def poll_loop() -> None:
    async with aiohttp.ClientSession(headers={"User-Agent": "srlb/1.0"}) as session:
        while True:
            try:
                await poll_once(session)
            except Exception as e:
                print(f"[poll] loop error: {e}")
            await asyncio.sleep(POLL_INTERVAL_S)


async def handle_index(_request: web.Request) -> web.Response:
    return web.Response(body=INDEX_PATH.read_bytes(), content_type="text/html")


async def handle_api(_request: web.Request) -> web.Response:
    return web.json_response(STATE)


def load_cached_state() -> None:
    if STATE_PATH.exists():
        try:
            cached = json.loads(STATE_PATH.read_text())
            STATE.update({
                "updated": cached.get("updated"),
                "season": cached.get("season"),
                "guilds": cached.get("guilds", []),
                "errors": cached.get("errors", []),
            })
            print(f"[init] loaded cached state: {len(STATE['guilds'])} guilds")
        except Exception as e:
            print(f"[init] failed to load cache: {e}")


async def main() -> None:
    load_cached_state()
    app = web.Application()
    app.router.add_get("/", handle_index)
    app.router.add_get("/api/leaderboard", handle_api)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", PORT)
    await site.start()
    print(f"[ready] http://localhost:{PORT}  (poll every {POLL_INTERVAL_S}s, {CONCURRENCY} concurrent)")

    await poll_loop()


if __name__ == "__main__":
    asyncio.run(main())
