# Deploying to Render

## The three constraints that shaped this setup

1. **No ffmpeg on Render's native Python runtime.** `engine/edit.py` shells out to
   `ffmpeg`/`ffprobe` for every concat and the outro append. So this deploys as a
   **Docker service**, not a native Python one.

2. **Render Cron Jobs cannot mount a web service's disk.** They run in their own
   container. A separate cron job would harvest into storage the UI could never read.
   So the 3-hourly cycle runs as a **thread inside the web service** (`SCHEDULE_HOURS=3`),
   which is the only process that can see the disk. `cron/cycle.sh` stays for local use.

3. **Container filesystems are wiped on every deploy and restart.** All state goes on a
   **persistent disk** at `/var/data`, and every module reads its path from
   `engine/paths.py` via `DATA_DIR`. Nothing writes next to the code.

## Services to create

| Render service | Plan | Why |
|---|---|---|
| Web Service (Docker) | **Starter or above** | Free tier has no persistent disk and sleeps after 15 min idle, which kills the scheduler |
| Persistent Disk, 10 GB at `/var/data` | included with Starter | reels.json is ~6 MB per harvest; renders are ~4 MB each |

You do **not** need a Render Cron Job, a database, or a Redis instance.

## Steps

1. Push this repo to GitHub (Render deploys from a repo).
2. Render Dashboard → **New → Blueprint**, point at the repo. `render.yaml` defines everything.
3. Set the four secrets in the dashboard (they are `sync: false`, so they are never in git):
   `HIKER_API_KEY`, `AZURE_API_KEY`, `AZURE_ENDPOINT`, `GOOGLE_API_KEY`, `ELEVENLABS_API_KEY`
4. Deploy. First boot creates the disk layout and starts with an empty UI.
5. Hit **Harvest** in the UI, or wait for the scheduler's first cycle.

## Seeding the disk with existing data

The 2,251 harvested reels and the prompt library are local. Either re-harvest on Render
(costs HikerAPI calls but takes ~4 min), or upload `data/` to the disk via Render Shell.

## Cost notes

- Veo renders are the expensive part. Keep `render` out of the scheduled stages.
- `PROMPTS_HOUR=4` keeps the ~198-prompt sweep to once daily, not every 3 hours.
- The HikerAPI disk cache lives on the persistent disk, so restarts do not re-pay for calls.

## Health

`GET /api/health` returns data dir, scheduler state and last run — use it as the
Render health check path (already set in `render.yaml`).
