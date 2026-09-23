"""Harvest reels from HikerAPI across genre x language hashtag seeds, then score virality."""
import os, sys, json, math, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import hiker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA, CONFIG as _CFG
CFG = _CFG
NOW = dt.datetime.now(dt.timezone.utc)


def load(name):
    with open(os.path.join(CFG, name)) as f:
        return yaml.safe_load(f)


def _audio_of(m):
    cm = m.get("clips_metadata") or {}
    mus = cm.get("music_info") or {}
    orig = cm.get("original_sound_info") or {}
    if mus:
        a = (mus.get("music_asset_info") or {})
        return {
            "kind": "music",
            "title": a.get("title"),
            "artist": a.get("display_artist"),
            "id": a.get("audio_cluster_id"),
        }
    if orig:
        return {
            "kind": "original",
            "title": orig.get("original_audio_title"),
            "artist": (orig.get("ig_artist") or {}).get("username"),
            "id": orig.get("audio_asset_id"),
            "reuse_count": orig.get("original_audio_subtype") and None,
        }
    return {"kind": None, "title": None, "artist": None, "id": None}


def normalize(m, genre, lang, seed):
    """HikerAPI v1 media -> flat scored record. Returns None for non-video."""
    if m.get("media_type") != 2:          # reels only
        return None
    u = m.get("user") or {}
    ts = m.get("taken_at_ts")
    if not ts:
        return None
    taken = dt.datetime.fromtimestamp(ts, dt.timezone.utc)
    age_days = max((NOW - taken).total_seconds() / 86400.0, 0.25)

    likes = int(m.get("like_count") or 0)
    comments = int(m.get("comment_count") or 0)
    plays = int(m.get("play_count") or 0)
    views = int(m.get("view_count") or 0)
    # play_count is often 0 on hashtag feeds; fall back to a likes-based reach proxy
    reach = max(plays, views, likes * 18)
    followers = int(u.get("follower_count") or 0)

    velocity = reach / age_days
    er = (likes + comments) / reach if reach else 0.0
    # the real trend signal: reach far beyond the account's own size
    outlier = (reach / followers) if followers > 500 else (1.0 if reach else 0.0)

    score = (
        math.log10(velocity + 10) * 3.0
        + math.log10(reach + 10) * 1.6
        + min(outlier, 60) * 0.22
        + min(er, 0.25) * 8
        - min(age_days / 30.0, 3.0) * 0.8
    )

    return {
        "code": m.get("code"),
        "url": f"https://www.instagram.com/reel/{m.get('code')}/",
        "genre": genre, "lang": lang, "seed": seed,
        "date": taken.date().isoformat(),
        "taken_at": taken.isoformat(),
        "age_days": round(age_days, 2),
        "username": u.get("username"),
        "followers": followers,
        "likes": likes, "comments": comments,
        "plays": plays, "views": views, "reach": reach,
        "velocity": round(velocity), "er": round(er, 4),
        "outlier": round(outlier, 2),
        "score": round(score, 3),
        "duration": round(float(m.get("video_duration") or 0), 1),
        "caption": (m.get("caption_text") or "")[:900],
        "audio": _audio_of(m),
        "video_url": m.get("video_url"),
        "thumb": m.get("thumbnail_url"),
    }


def harvest(window_days=45, workers=8, amount=30):
    tax = load("taxonomy.yaml")
    jobs = []
    for genre, langs in tax["seeds"].items():
        for lang, tags in langs.items():
            for t in tags:
                jobs.append((genre, lang, t, "top"))
                if lang == "_global":
                    jobs.append((genre, lang, t, "recent"))

    print(f"harvesting {len(jobs)} hashtag feeds with {workers} workers...")
    rows, seen = [], set()

    def run(job):
        genre, lang, tag, mode = job
        fn = hiker.hashtag_top if mode == "top" else hiker.hashtag_recent
        return job, fn(tag, amount)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run, j) for j in jobs]
        for f in as_completed(futs):
            (genre, lang, tag, mode), items = f.result()
            done += 1
            if done % 25 == 0:
                print(f"  {done}/{len(jobs)} feeds | {len(rows)} reels")
            for m in items or []:
                r = normalize(m, genre, lang, tag)
                if not r or r["code"] in seen:
                    continue
                if r["age_days"] > window_days:
                    continue
                seen.add(r["code"])
                rows.append(r)

    rows.sort(key=lambda r: -r["score"])
    out = os.path.join(DATA, "scored", "reels.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w") as f:
        json.dump({"generated_at": NOW.isoformat(), "window_days": window_days,
                   "count": len(rows), "api": hiker.STATS, "rows": rows}, f, indent=1)
    print(f"\n{len(rows)} reels in window -> {out}")
    print("api stats:", hiker.STATS)
    return rows


if __name__ == "__main__":
    harvest(window_days=int(sys.argv[1]) if len(sys.argv) > 1 else 45)
