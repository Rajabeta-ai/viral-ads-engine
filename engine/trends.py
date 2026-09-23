"""Cluster harvested reels into recreatable FORMATS and emit a date x language x state trend report."""
import os, sys, json, collections, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA

SYS = """You are a performance-creative strategist for Indian short-form video.
You classify Instagram Reels into REPRODUCIBLE FORMATS - the structural recipe a different
brand could re-shoot, not the subject matter. You are blunt and specific. You never invent
metrics. You write for an audience of Bharat (tier-2/3 India) ad buyers."""


def classify_batch(reels):
    payload = [{
        "i": i, "genre": r["genre"], "lang": r["lang"], "date": r["date"],
        "reach": r["reach"], "followers": r["followers"], "outlier": r["outlier"],
        "dur": r["duration"], "audio": r["audio"].get("title"),
        "caption": (r["caption"] or "")[:320],
    } for i, r in enumerate(reels)]

    prompt = f"""Here are {len(reels)} Instagram Reels that over-performed their account size
(outlier = views divided by follower count; >20 means the FORMAT carried it, not the audience).

{json.dumps(payload, ensure_ascii=False)}

For each reel return an object:
{{"i": <index>,
  "format": "<4-8 word name of the reproducible structural format, e.g. 'POV text-over-static with slow zoom'>",
  "hook_mechanic": "<what makes someone stop scrolling in the first 1.5s>",
  "recreatable_by_ai": <true|false - can a text-to-video model like Veo 3.1 rebuild this without real footage of real people/places?>,
  "emotional_job": "<one of: loneliness, boredom, pride, devotion, awe, nostalgia, humour, tribal-identity, fomo, reassurance, curiosity, catharsis>"}}

Return a JSON array only."""
    return llm.ask_json(prompt, system=SYS)


def select(rows, top_n=180, per_lang=0):
    """Pick which reels to classify.

    Global top-N is biased toward high-volume languages: a straight top-180 left Gujarati
    and Telugu with 5 reels each while Hindi got 30. per_lang>0 takes that many from EACH
    language first, so every language gets a fair read, then fills the remainder globally.
    """
    pool = [r for r in rows if r.get("enriched") and r.get("followers", 0) > 0]
    pool.sort(key=lambda r: -r["score"])
    if not per_lang:
        return pool[:top_n]

    chosen, seen = [], set()
    bylang = collections.defaultdict(list)
    for r in pool:
        bylang[r["lang"]].append(r)
    for lang in sorted(bylang):
        for r in bylang[lang][:per_lang]:
            if r["code"] not in seen:
                seen.add(r["code"]); chosen.append(r)
    for r in pool:                       # top up to top_n with the best of the rest
        if len(chosen) >= top_n:
            break
        if r["code"] not in seen:
            seen.add(r["code"]); chosen.append(r)
    chosen.sort(key=lambda r: -r["score"])
    dist = collections.Counter(r["lang"] for r in chosen)
    print(f"per-language quota {per_lang}: {dict(sorted(dist.items()))}")
    return chosen


def build(top_n=180, batch=30, per_lang=0):
    d = json.load(open(os.path.join(DATA, "scored", "reels.json")))
    rows = d["rows"]
    pool = select(rows, top_n, per_lang)
    print(f"classifying {len(pool)} high-signal reels in batches of {batch}...")

    chunks = [pool[i:i + batch] for i in range(0, len(pool), batch)]

    def run(idx_chunk):
        idx, chunk = idx_chunk
        try:
            return idx, classify_batch(chunk)
        except Exception as e:
            print(f"  ! batch {idx} failed: {e}")
            return idx, []

    results = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(run, (i, c)): i for i, c in enumerate(chunks)}
        for f in as_completed(futs):
            idx, res = f.result()
            results[idx] = res
            print(f"  batch {idx+1}/{len(chunks)} -> {len(res)} classified")

    for idx, res in results.items():
        base = idx * batch
        for item in res or []:
            try:
                r = pool[base + int(item["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            r["format"] = item.get("format")
            r["hook_mechanic"] = item.get("hook_mechanic")
            r["ai_recreatable"] = item.get("recreatable_by_ai")
            r["emotional_job"] = item.get("emotional_job")

    classified = [r for r in pool if r.get("format")]
    print(f"\n{len(classified)} reels classified")

    tax = yaml.safe_load(open(os.path.join(ROOT, "config", "taxonomy.yaml")))
    langmap = tax["languages"]

    # ---- date x lang x genre rollup
    bydate = collections.defaultdict(lambda: collections.defaultdict(list))
    for r in classified:
        bydate[r["date"]][(r["genre"], r["lang"])].append(r)

    timeline = []
    for date in sorted(bydate, reverse=True):
        entries = []
        for (genre, lang), rs in sorted(bydate[date].items(), key=lambda kv: -sum(x["score"] for x in kv[1])):
            rs.sort(key=lambda r: -r["score"])
            meta = langmap.get(lang, {})
            entries.append({
                "genre": genre, "lang": lang,
                "language": meta.get("name", "Pan-India"),
                "states": meta.get("states", ["Pan-India"]),
                "n": len(rs),
                "top_format": rs[0].get("format"),
                "hook_mechanic": rs[0].get("hook_mechanic"),
                "emotional_job": rs[0].get("emotional_job"),
                "ai_recreatable": rs[0].get("ai_recreatable"),
                "peak_reach": rs[0]["reach"], "peak_outlier": rs[0]["outlier"],
                "exemplar": rs[0]["url"], "exemplar_user": rs[0]["username"],
                "formats": list(dict.fromkeys([x.get("format") for x in rs if x.get("format")]))[:5],
            })
        timeline.append({"date": date, "entries": entries})

    # ---- format leaderboard
    fc = collections.defaultdict(lambda: {"n": 0, "reach": 0, "outliers": [], "genres": set(),
                                          "langs": set(), "jobs": set(), "ai": 0, "ex": None})
    for r in classified:
        k = r["format"]
        s = fc[k]
        s["n"] += 1; s["reach"] += r["reach"]; s["outliers"].append(r["outlier"])
        s["genres"].add(r["genre"]); s["langs"].add(r["lang"])
        if r.get("emotional_job"): s["jobs"].add(r["emotional_job"])
        if r.get("ai_recreatable"): s["ai"] += 1
        if not s["ex"] or r["score"] > s["ex"]["score"]: s["ex"] = r

    leaderboard = sorted([{
        "format": k, "uses": v["n"], "total_reach": v["reach"],
        "median_outlier": round(sorted(v["outliers"])[len(v["outliers"]) // 2], 1),
        "genres": sorted(v["genres"]), "langs": sorted(v["langs"]),
        "emotional_jobs": sorted(v["jobs"]),
        "ai_recreatable_pct": round(100 * v["ai"] / v["n"]),
        "exemplar": v["ex"]["url"], "exemplar_reach": v["ex"]["reach"],
        "exemplar_followers": v["ex"]["followers"],
    } for k, v in fc.items()], key=lambda x: (-x["total_reach"], -x["uses"]))

    out = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "classified": len(classified), "timeline": timeline, "leaderboard": leaderboard}
    fp = os.path.join(DATA, "trends", "trends.json")
    json.dump(out, open(fp, "w"), indent=1, ensure_ascii=False)
    json.dump(d, open(os.path.join(DATA, "scored", "reels.json"), "w"), indent=1, ensure_ascii=False)
    print(f"-> {fp}")

    print("\nTOP 15 FORMATS BY TOTAL REACH:")
    for f in leaderboard[:15]:
        print(f"  {f['total_reach']:>10,} {f['uses']:>3}x med_out={f['median_outlier']:>6} ai={f['ai_recreatable_pct']:>3}% "
              f"[{','.join(f['genres'])[:22]:22}] {f['format'][:56]}")
    return out


if __name__ == "__main__":
    # usage: trends.py [top_n] [per_lang]   e.g. "trends.py 240 15" = >=15 per language
    build(top_n=int(sys.argv[1]) if len(sys.argv) > 1 else 180,
          per_lang=int(sys.argv[2]) if len(sys.argv) > 2 else 0)
