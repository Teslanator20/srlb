import { readFile, writeFile } from "node:fs/promises";

const GUILDS_FILE = "guilds.txt";
const DATA_FILE = "data.json";
const CONCURRENCY = 6;
const TIMEOUT_MS = 20_000;
const UA = "srlb/1.0 (+https://github.com)";

async function loadGuilds() {
  const raw = await readFile(GUILDS_FILE, "utf8");
  const seen = new Set();
  const out = [];
  for (const line of raw.split(/\r?\n/)) {
    const name = line.trim();
    if (!name || name.startsWith("#")) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    out.push(name);
  }
  return out;
}

async function fetchJson(url) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), TIMEOUT_MS);
  try {
    const r = await fetch(url, { headers: { "User-Agent": UA }, signal: ctrl.signal });
    if (r.status === 404) return { status: 404, data: null };
    if (!r.ok) return { status: r.status, data: null };
    return { status: 200, data: await r.json() };
  } finally {
    clearTimeout(t);
  }
}

async function fetchGuild(entry) {
  const q = encodeURIComponent(entry);
  try {
    let res = await fetchJson(`https://api.wynncraft.com/v3/guild/${q}`);
    if (res.status === 200) return { entry, data: res.data, error: null };
    if (res.status !== 404) return { entry, data: null, error: `HTTP ${res.status}` };
    res = await fetchJson(`https://api.wynncraft.com/v3/guild/prefix/${q}`);
    if (res.status === 200) return { entry, data: res.data, error: null };
    if (res.status === 404) return { entry, data: null, error: "not found" };
    return { entry, data: null, error: `HTTP ${res.status}` };
  } catch (e) {
    return { entry, data: null, error: String(e?.message ?? e) };
  }
}

function latestSeason(seasonRanks) {
  if (!seasonRanks) return null;
  let max = null;
  for (const k of Object.keys(seasonRanks)) {
    const n = Number(k);
    if (Number.isFinite(n) && (max === null || n > max)) max = n;
  }
  return max;
}

function buildRow(entry, data) {
  const season = latestSeason(data.seasonRanks);
  const sr = season !== null ? data.seasonRanks[String(season)] : null;
  return {
    uuid: data.uuid,
    name: data.name ?? entry,
    prefix: data.prefix ?? "",
    level: data.level ?? null,
    xpPercent: data.xpPercent ?? null,
    territories: data.territories ?? 0,
    wars: data.wars ?? 0,
    raids: data.raids ?? 0,
    season,
    rating: Number(sr?.rating ?? 0),
  };
}

async function pool(items, n, fn) {
  const results = new Array(items.length);
  let idx = 0;
  async function worker() {
    while (true) {
      const i = idx++;
      if (i >= items.length) return;
      results[i] = await fn(items[i]);
    }
  }
  await Promise.all(Array.from({ length: Math.min(n, items.length) }, worker));
  return results;
}

async function main() {
  const guilds = await loadGuilds();
  if (!guilds.length) {
    console.error("guilds.txt is empty");
    process.exit(1);
  }

  const results = await pool(guilds, CONCURRENCY, fetchGuild);

  const rows = [];
  const errors = [];
  const seenUuids = new Set();
  const seasonsSeen = new Map();

  for (const r of results) {
    if (r.error || !r.data) {
      errors.push({ name: r.entry, error: r.error ?? "no data" });
      continue;
    }
    const row = buildRow(r.entry, r.data);
    if (!row.uuid || seenUuids.has(row.uuid)) continue;
    seenUuids.add(row.uuid);
    if (row.season !== null) {
      seasonsSeen.set(row.season, (seasonsSeen.get(row.season) ?? 0) + 1);
    }
    rows.push(row);
  }

  let currentSeason = null;
  let best = -1;
  for (const [s, c] of seasonsSeen) {
    if (c > best) { best = c; currentSeason = s; }
  }
  for (const r of rows) r.isCurrentSeason = r.season === currentSeason;

  rows.sort((a, b) => b.rating - a.rating || a.name.localeCompare(b.name));
  rows.forEach((r, i) => { r.rank = i + 1; });

  const out = {
    updated: new Date().toISOString(),
    season: currentSeason,
    guilds: rows,
    errors,
  };

  await writeFile(DATA_FILE, JSON.stringify(out, null, 2) + "\n");
  console.log(`OK season=${currentSeason} guilds=${rows.length} errors=${errors.length} top=${rows[0]?.rating ?? 0}`);
}

main().catch((e) => {
  console.error(e);
  process.exit(1);
});
