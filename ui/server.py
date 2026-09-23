#!/usr/bin/env python3
"""Zero-dependency control plane for the viral-ads engine.

Serves the monitor UI, exposes pipeline state as JSON, and runs pipeline stages as
tracked subprocesses. Deliberately stdlib-only so it drops into any backend host.
Swap BaseHTTPRequestHandler for FastAPI routes when you move it - the JSON shapes
are already the contract.
"""
import os, sys, json, threading, subprocess, time, uuid, mimetypes, cgi, shutil
import datetime as dt
import yaml
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
from engine.paths import DATA
UI = os.path.join(ROOT, "ui")
PORT = int(os.environ.get("PORT", "8765"))
DEFAULT_DAYS = int(os.environ.get("WINDOW_DAYS", "3"))

JOBS = {}          # job_id -> dict
JOBS_LOCK = threading.Lock()

STAGES = {
    "harvest":  [sys.executable, os.path.join(ROOT, "engine", "harvest.py"), "45"],
    "enrich":   [sys.executable, os.path.join(ROOT, "engine", "enrich.py"), "600", "40"],
    "trends":   [sys.executable, os.path.join(ROOT, "engine", "trends.py"), "240", "15"],
    "cluster":  [sys.executable, os.path.join(ROOT, "engine", "cluster.py"), "14"],
    "prompts":  [sys.executable, os.path.join(ROOT, "engine", "prompts.py"), "all", "2", "10"],
    "render":   [sys.executable, os.path.join(ROOT, "engine", "render.py")],
}


def _env():
    e = os.environ.copy()
    envf = os.path.join(ROOT, ".env")
    if os.path.exists(envf):
        for line in open(envf):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                e[k.strip()] = v.strip()
    return e


def start_job(stage, extra=None):
    if stage not in STAGES:
        raise KeyError(stage)
    jid = f"{stage}-{uuid.uuid4().hex[:8]}"
    cmd = list(STAGES[stage]) + (extra or [])
    job = {"id": jid, "stage": stage, "cmd": " ".join(cmd[1:]), "status": "running",
           "started": time.time(), "ended": None, "log": [], "rc": None}
    with JOBS_LOCK:
        JOBS[jid] = job

    def run():
        try:
            p = subprocess.Popen(cmd, cwd=ROOT, env=_env(), stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, text=True, bufsize=1)
            for line in p.stdout:
                with JOBS_LOCK:
                    job["log"].append(line.rstrip()[:500])
                    if len(job["log"]) > 900:
                        del job["log"][:300]
            p.wait()
            with JOBS_LOCK:
                job["rc"] = p.returncode
                job["status"] = "done" if p.returncode == 0 else "failed"
        except Exception as e:
            with JOBS_LOCK:
                job["status"] = "failed"; job["log"].append(f"EXC {e!r}")
        finally:
            with JOBS_LOCK:
                job["ended"] = time.time()

    threading.Thread(target=run, daemon=True).start()
    return job


def _read(rel, default=None):
    fp = os.path.join(DATA, rel)
    if not os.path.exists(fp):
        return default
    try:
        return json.load(open(fp))
    except Exception:
        return default


def _cutoff(days):
    return (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=days)).date().isoformat()


def _rollup(rows, key, langmap):
    """Aggregate fresh reels by language or genre for the dedicated UI tabs."""
    groups = {}
    for r in rows:
        groups.setdefault(r.get(key), []).append(r)
    out = []
    for k, rs in groups.items():
        rs.sort(key=lambda r: -r.get("outlier", 0))
        meta = langmap.get(k, {}) if key == "lang" else {}
        fmts = {}
        for r in rs:
            if r.get("format"):
                f = fmts.setdefault(r["format"], {"format": r["format"], "n": 0, "reach": 0,
                                                  "outlier": 0, "job": r.get("emotional_job"),
                                                  "ai": r.get("ai_recreatable"), "url": r["url"]})
                f["n"] += 1; f["reach"] += r["reach"]
                f["outlier"] = max(f["outlier"], r.get("outlier", 0))
        top_f = sorted(fmts.values(), key=lambda f: -f["outlier"])[:6]
        out.append({
            "key": k,
            "label": meta.get("name") or (k if key == "genre" else "Pan-India"),
            "states": meta.get("states", ["Pan-India"]) if key == "lang" else [],
            "n": len(rs),
            "classified": sum(1 for r in rs if r.get("format")),
            "total_reach": sum(r["reach"] for r in rs),
            "peak_outlier": round(max((r.get("outlier", 0) for r in rs), default=0), 1),
            "genres": _count(rs, "genre") if key == "lang" else {},
            "langs": _count(rs, "lang") if key == "genre" else {},
            "jobs": _count([r for r in rs if r.get("emotional_job")], "emotional_job"),
            "top_formats": top_f,
            "top_reels": [{kk: r.get(kk) for kk in
                           ("url", "date", "username", "followers", "reach", "outlier",
                            "format", "hook_mechanic", "genre", "lang", "caption")}
                          for r in rs[:8]],
        })
    out.sort(key=lambda g: -g["total_reach"])
    return out


def state(days=DEFAULT_DAYS):
    reels = _read("scored/reels.json", {}) or {}
    trends = _read("trends/trends.json", {}) or {}
    prompts = _read("prompts/prompts.json", {}) or {}
    flag = _read("prompts/flagship.json", {}) or {}
    if flag.get("prompts"):
        merged = list(flag["prompts"]) + list(prompts.get("prompts", []))
        prompts = dict(prompts)
        prompts["prompts"] = merged
        prompts["count"] = len(merged)
        prompts["flagship"] = len(flag["prompts"])
    comp = _read("trends/competitors.json", {}) or {}

    try:
        langmap = yaml.safe_load(open(os.path.join(ROOT, "config", "taxonomy.yaml")))["languages"]
    except Exception:
        langmap = {}

    cut = _cutoff(days)
    all_rows = reels.get("rows", [])
    # Freshness window: nothing older than `days` is shown. Data stays on disk.
    rows = [r for r in all_rows if (r.get("date") or "") >= cut]
    timeline = [d for d in trends.get("timeline", []) if (d.get("date") or "") >= cut]

    rend = os.path.join(DATA, "renders")
    renders, cut_ts = [], time.time() - days * 86400
    if os.path.isdir(rend):
        for f in sorted(os.listdir(rend)):
            if f.endswith((".mp4", ".mov")):
                fp = os.path.join(rend, f)
                if os.path.getmtime(fp) >= cut_ts:
                    renders.append({"file": f, "bytes": os.path.getsize(fp),
                                    "mtime": os.path.getmtime(fp)})

    outro = os.path.join(DATA, "assets", "outro.mp4")
    with JOBS_LOCK:
        jobs = sorted([{k: v for k, v in j.items() if k != "log"} for j in JOBS.values()],
                      key=lambda j: -j["started"])[:40]
    sched = _read(".schedule.json", None) or {}
    return {
        "window_days": days, "cutoff": cut,
        "harvest": {"count": len(rows), "total_on_disk": len(all_rows),
                    "generated_at": reels.get("generated_at"),
                    "window_days": reels.get("window_days"),
                    "api": reels.get("api", {}), "enriched": reels.get("enriched", 0)},
        "genres": _count(rows, "genre"), "langs": _count(rows, "lang"),
        "dates": _count(rows, "date"),
        "by_language": _rollup(rows, "lang", langmap),
        "by_genre": _rollup(rows, "genre", langmap),
        "top_reels": [{k: r.get(k) for k in
                       ("code", "url", "genre", "lang", "date", "username", "followers",
                        "reach", "outlier", "score", "format", "hook_mechanic",
                        "emotional_job", "caption")} for r in rows[:60]],
        "trends": {"classified": trends.get("classified", 0),
                   "timeline": timeline[:20],
                   "canonical_formats": trends.get("canonical_formats", []),
                   "leaderboard": trends.get("leaderboard", [])[:40]},
        "prompts": prompts, "competitors": comp, "renders": renders,
        "outro": {"present": os.path.exists(outro),
                  "bytes": os.path.getsize(outro) if os.path.exists(outro) else 0},
        "jobs": jobs, "stages": list(STAGES), "schedule": sched,
    }


def _count(rows, key):
    c = {}
    for r in rows:
        c[r.get(key)] = c.get(r.get(key), 0) + 1
    return dict(sorted(c.items(), key=lambda kv: -kv[1]))


class H(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        elif isinstance(body, str):
            body = body.encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        u = urlparse(self.path)
        p, q = u.path, parse_qs(u.query)
        if p in ("/", "/index.html"):
            fp = os.path.join(UI, "index.html")
            return self._send(200, open(fp, "rb").read(), "text/html; charset=utf-8")
        if p == "/api/health":
            return self._send(200, {"ok": True, "data_dir": DATA,
                                    "scheduler": SCHED.get("enabled"),
                                    "last_run": SCHED.get("last_run"),
                                    "running": SCHED.get("running")})
        if p == "/api/state":
            try:
                days = max(1, min(90, int((q.get("days") or [DEFAULT_DAYS])[0])))
            except (TypeError, ValueError):
                days = DEFAULT_DAYS
            return self._send(200, state(days))
        if p == "/api/job":
            jid = (q.get("id") or [""])[0]
            with JOBS_LOCK:
                j = JOBS.get(jid)
                return self._send(200 if j else 404, j or {"error": "no such job"})
        if p.startswith("/renders/"):
            fp = os.path.join(DATA, "renders", os.path.basename(p))
            if not os.path.exists(fp):
                return self._send(404, {"error": "not found"})
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            return self._send(200, open(fp, "rb").read(), ctype)
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        u = urlparse(self.path)
        p = u.path
        if p.startswith("/api/run/"):
            stage = p.rsplit("/", 1)[-1]
            n = int(self.headers.get("Content-Length") or 0)
            extra = []
            if n:
                try:
                    extra = (json.loads(self.rfile.read(n)) or {}).get("args", [])
                except Exception:
                    pass
            try:
                return self._send(200, start_job(stage, [str(x) for x in extra]))
            except KeyError:
                return self._send(400, {"error": f"unknown stage {stage}"})
        if p == "/api/outro":
            ctype = self.headers.get("Content-Type", "")
            n = int(self.headers.get("Content-Length") or 0)
            dst = os.path.join(DATA, "assets", "outro.mp4")
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            if "multipart/form-data" in ctype:
                form = cgi.FieldStorage(fp=self.rfile, headers=self.headers,
                                        environ={"REQUEST_METHOD": "POST", "CONTENT_TYPE": ctype})
                item = form["file"] if "file" in form else None
                if item is None:
                    return self._send(400, {"error": "no file field"})
                with open(dst, "wb") as f:
                    shutil.copyfileobj(item.file, f)
            else:
                with open(dst, "wb") as f:
                    f.write(self.rfile.read(n))
            return self._send(200, {"ok": True, "bytes": os.path.getsize(dst)})
        return self._send(404, {"error": "not found"})


SCHED = {"enabled": False, "last_run": None, "running": False, "hours": 0}


def _scheduler():
    """Run the refresh cycle every SCHEDULE_HOURS. Only the web service can see the
    persistent disk on Render, so the schedule runs here rather than as a Render Cron Job."""
    hours = float(os.environ.get("SCHEDULE_HOURS", "0") or 0)
    if hours <= 0:
        return
    prompts_hour = int(os.environ.get("PROMPTS_HOUR", "4"))
    SCHED.update({"enabled": True, "hours": hours})
    time.sleep(20)                                  # let the service finish booting
    while True:
        try:
            stages = ["harvest", "enrich", "trends", "cluster"]
            h = dt.datetime.now().hour
            if prompts_hour <= h < prompts_hour + hours:
                stages.append("prompts")
            SCHED["running"] = True
            for st in stages:
                j = start_job(st)
                while True:
                    with JOBS_LOCK:
                        done = j["status"] != "running"
                    if done:
                        break
                    time.sleep(5)
                if j["status"] == "failed":
                    break
            SCHED.update({"running": False,
                          "last_run": dt.datetime.now(dt.timezone.utc).isoformat()})
            _write_schedule_state()
        except Exception as e:
            SCHED["running"] = False
            print(f"scheduler error: {e!r}", flush=True)
        time.sleep(hours * 3600)


def _write_schedule_state():
    try:
        json.dump({"last_run": SCHED["last_run"], "last_status": "ok",
                   "next_hint": f"every {SCHED['hours']}h", "source": "internal-scheduler"},
                  open(os.path.join(DATA, ".schedule.json"), "w"), indent=1)
    except Exception:
        pass


if __name__ == "__main__":
    threading.Thread(target=_scheduler, daemon=True).start()
    print(f"viral-ads monitor -> http://localhost:{PORT}  data={DATA}"
          f"  schedule={os.environ.get('SCHEDULE_HOURS','off')}h", flush=True)
    ThreadingHTTPServer(("0.0.0.0", PORT), H).serve_forever()
