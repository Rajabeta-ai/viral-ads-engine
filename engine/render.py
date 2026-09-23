"""Render prompts -> finished ads.

Pipeline per prompt:  Veo 3.1 clip  ->  [optional ElevenLabs VO mixed under]  ->  team outro appended.
The outro is NEVER generated - it is data/assets/outro.mp4, supplied by the team.
Renders are idempotent: an existing final file is skipped unless --force.
"""
import os, sys, json, time, argparse, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import veo, voice, edit

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA
RENDERS = os.path.join(DATA, "renders")
OUTRO = os.path.join(DATA, "assets", "outro.mp4")
STATUS = os.path.join(RENDERS, "_status.json")

VOICE_BY_APP = {"dostii": "female_warm", "prabhuji": "male_narrator", "cricsignal": "male_deep"}


def _status(update=None):
    os.makedirs(RENDERS, exist_ok=True)
    s = {}
    if os.path.exists(STATUS):
        try:
            s = json.load(open(STATUS))
        except Exception:
            s = {}
    if update:
        s.update(update)
        json.dump(s, open(STATUS, "w"), indent=1)
    return s


def render_one(p, with_vo=False, attach=True, seconds=8, force=False):
    pid = p["id"]
    final = os.path.join(RENDERS, f"{pid}.mp4")
    if os.path.exists(final) and not force:
        return {"id": pid, "status": "skipped", "file": final}

    body = os.path.join(RENDERS, "_body", f"{pid}.mp4")
    os.makedirs(os.path.dirname(body), exist_ok=True)
    t0 = time.time()
    try:
        _status({pid: {"status": "generating", "started": t0}})
        if not (os.path.exists(body) and not force):
            veo.generate(p["veo_prompt"], body, seconds=seconds, aspect="9:16")

        stage = body
        if with_vo and (p.get("vo_script") or "").strip():
            _status({pid: {"status": "voicing", "started": t0}})
            mp3 = os.path.join(RENDERS, "_vo", f"{pid}.mp3")
            voice.tts(p["vo_script"], mp3, voice=VOICE_BY_APP.get(p.get("app"), "female_warm"))
            mixed = os.path.join(RENDERS, "_mix", f"{pid}.mp4")
            os.makedirs(os.path.dirname(mixed), exist_ok=True)
            edit.add_voiceover(stage, mp3, mixed)
            stage = mixed

        if attach and os.path.exists(OUTRO):
            _status({pid: {"status": "attaching_outro", "started": t0}})
            edit.attach_outro(stage, OUTRO, final)
            outro = True
        else:
            edit.normalize(stage, final)
            outro = False

        dur = edit.probe(final).get("format", {}).get("duration")
        res = {"id": pid, "status": "done", "file": final, "outro": outro,
               "seconds": round(time.time() - t0), "duration": dur}
    except Exception as e:
        res = {"id": pid, "status": "failed", "error": f"{type(e).__name__}: {e}",
               "trace": traceback.format_exc()[-600:], "seconds": round(time.time() - t0)}
    _status({pid: res})
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", nargs="*", help="prompt ids to render (default: --limit best)")
    ap.add_argument("--limit", type=int, default=3)
    ap.add_argument("--app", help="filter by app")
    ap.add_argument("--origin", help="filter by origin: trend|insight|competitor")
    ap.add_argument("--workers", type=int, default=2)
    ap.add_argument("--seconds", type=int, default=8)
    ap.add_argument("--vo", action="store_true", help="mix an ElevenLabs VO under the Veo audio")
    ap.add_argument("--no-outro", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()

    doc = json.load(open(os.path.join(DATA, "prompts", "prompts.json")))
    ps = doc["prompts"]
    if a.app:    ps = [p for p in ps if p.get("app") == a.app]
    if a.origin: ps = [p for p in ps if p.get("origin") == a.origin]
    if a.ids:    ps = [p for p in ps if p["id"] in a.ids]
    else:        ps = ps[:a.limit]

    if not os.path.exists(OUTRO) and not a.no_outro:
        print(f"! no outro at {OUTRO} - rendering bodies only. Upload one in the UI, then re-run.")

    print(f"rendering {len(ps)} prompts (workers={a.workers}, vo={a.vo})")
    ok = 0
    with ThreadPoolExecutor(max_workers=a.workers) as ex:
        futs = {ex.submit(render_one, p, a.vo, not a.no_outro, a.seconds, a.force): p for p in ps}
        for f in as_completed(futs):
            r = f.result()
            ok += r["status"] in ("done", "skipped")
            mark = {"done": "OK", "skipped": "--", "failed": "!!"}[r["status"]]
            print(f"  {mark} {r['id']:22} {r['status']:9} {r.get('seconds','')}s "
                  f"{r.get('error','') or os.path.basename(r.get('file',''))}")
    print(f"\n{ok}/{len(ps)} succeeded -> {RENDERS}")


if __name__ == "__main__":
    main()
