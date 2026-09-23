"""Fetch follower counts for top creators and recompute the outlier/virality score.

Hashtag feeds return a truncated user object (no follower_count), so reach-vs-account-size
- the signal that distinguishes a viral FORMAT from a big account - is unavailable until
we look each creator up directly.
"""
import os, sys, json, math
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import hiker

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA
FP = os.path.join(DATA, "scored", "reels.json")


# A tiny account is the STRONGEST evidence that the format carried the reel, not the
# audience - so they must not be discarded. But dividing by 7 followers explodes, so the
# denominator has a floor. `enriched` records that we actually completed a creator lookup,
# which is what separates "genuinely tiny" from "lookup returned nothing".
FOLLOWER_FLOOR = 50


def rescore(r):
    reach, age = r["reach"], max(r["age_days"], 0.25)
    followers = r.get("followers") or 0
    velocity = reach / age
    er = (r["likes"] + r["comments"]) / reach if reach else 0.0
    if r.get("enriched") and followers > 0:
        outlier = reach / max(followers, FOLLOWER_FLOOR)
    else:
        outlier = 0.0
    r["velocity"], r["er"] = round(velocity), round(er, 4)
    r["outlier"] = round(outlier, 2)
    r["score"] = round(
        math.log10(velocity + 10) * 3.0
        + math.log10(reach + 10) * 1.6
        + min(outlier, 60) * 0.22
        + min(er, 0.25) * 8
        - min(age / 30.0, 3.0) * 0.8, 3)
    return r


def pick(rows, top_n, per_lang):
    """Global top-N enriches mostly Hindi and pan-India, starving the smaller languages of
    follower data - which then starves the classifier. per_lang takes that many per language
    first so every language gets comparable coverage."""
    ranked = sorted(rows, key=lambda r: -r["score"])
    if not per_lang:
        return ranked[:top_n]
    bylang, chosen, seen = {}, [], set()
    for r in ranked:
        bylang.setdefault(r["lang"], []).append(r)
    for lang in sorted(bylang):
        for r in bylang[lang][:per_lang]:
            if r["code"] not in seen:
                seen.add(r["code"]); chosen.append(r)
    for r in ranked:
        if len(chosen) >= top_n:
            break
        if r["code"] not in seen:
            seen.add(r["code"]); chosen.append(r)
    import collections as _c
    print(f"per-language quota {per_lang}: {dict(sorted(_c.Counter(r['lang'] for r in chosen).items()))}")
    return chosen


def main(top_n=300, workers=8, per_lang=0):
    d = json.load(open(FP))
    rows = d["rows"]
    targets = pick(rows, top_n, per_lang)
    users = sorted({r["username"] for r in targets if r.get("username")})
    print(f"enriching {len(users)} creators from top {top_n} reels...")

    info = {}
    def fetch(u):
        return u, hiker.user_by_username(u)

    done = 0
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(fetch, u) for u in users]):
            u, data = f.result()
            done += 1
            if done % 50 == 0:
                print(f"  {done}/{len(users)}")
            if data:
                info[u] = {
                    "followers": int(data.get("follower_count") or 0),
                    "verified": bool(data.get("is_verified")),
                    "media_count": data.get("media_count"),
                    "category": data.get("category") or data.get("category_name"),
                    "bio": (data.get("biography") or "")[:220],
                }

    hit = 0
    for r in rows:
        meta = info.get(r.get("username"))
        if meta:
            r["followers"] = meta["followers"]
            r["verified"] = meta["verified"]
            r["creator_category"] = meta["category"]
            r["creator_bio"] = meta["bio"]
            r["enriched"] = True
            hit += 1
        rescore(r)

    rows.sort(key=lambda r: -r["score"])
    d["rows"] = rows
    d["enriched"] = hit
    d["api"] = hiker.STATS
    json.dump(d, open(FP, "w"), indent=1)
    print(f"enriched {hit} reels | api {hiker.STATS}")

    print("\nTOP 20 AFTER RESCORE (outlier = reach / followers):")
    for r in rows[:20]:
        print(f"  {r['score']:6.2f} {r['genre'][:7]:7} {r['lang']:5} @{str(r['username'])[:19]:19} "
              f"reach={r['reach']:>9,} f={r['followers']:>8,} out={r['outlier']:>7} {r['date']}")


if __name__ == "__main__":
    # usage: enrich.py [top_n] [per_lang]   e.g. "enrich.py 560 40" = 40 per language
    main(top_n=int(sys.argv[1]) if len(sys.argv) > 1 else 300,
         per_lang=int(sys.argv[2]) if len(sys.argv) > 2 else 0)
