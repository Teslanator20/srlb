# srlb — Wynncraft Seasonal Rating Leaderboard

Static site that shows the current-season `seasonRanks[latest].rating` for a list of guilds.
Data is refreshed every 5 minutes by a GitHub Actions workflow that polls
`api.wynncraft.com/v3/guild/<name|prefix>` and commits `data.json` back to the repo.

## Files

- `guilds.txt` — list of guild names or prefixes, one per line (`#` = comment)
- `poll.js` — fetches each guild and writes `data.json`
- `index.html` — static frontend, loads `data.json`
- `.github/workflows/poll.yml` — cron `*/5 * * * *`
- `app.py` — local dev server (optional, runs the same logic in Python via aiohttp)

## Local dev

```bash
node poll.js          # writes data.json
# or: python app.py   # local server on :8080
```

## Hosting

GitHub Pages serves the repo root. The frontend fetches `data.json` from the same origin.
