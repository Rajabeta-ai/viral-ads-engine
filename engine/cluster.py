"""Collapse the long tail of per-reel format labels into a small set of canonical, reusable formats."""
import os, sys, json, collections
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import llm

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA

SYS = ("You are a performance-creative strategist. You group short-form video formats by their "
       "STRUCTURAL RECIPE - the shot sequence and hook mechanic - not by subject matter. "
       "A bhakti reel and a cricket reel can share a format.")


def build(n_clusters=14):
    t = json.load(open(os.path.join(DATA, "trends", "trends.json")))
    lb = t["leaderboard"]
    listing = [{"format": f["format"], "reach": f["total_reach"], "uses": f["uses"],
                "genres": f["genres"], "jobs": f["emotional_jobs"],
                "ai_pct": f["ai_recreatable_pct"]} for f in lb]

    prompt = f"""These are {len(listing)} format labels extracted from Instagram Reels that
over-performed their own account size in India (bhakti / cricket / companionship verticals).

{json.dumps(listing, ensure_ascii=False)}

Collapse them into exactly {n_clusters} CANONICAL FORMATS. Each canonical format must be a
recipe someone could hand to a video team and get a shootable result.

Return a JSON array of:
{{"canonical": "<3-6 word format name>",
  "recipe": "<the shot-by-shot structure in one tight sentence: what happens at 0-1.5s, then what, then what>",
  "hook_mechanic": "<the specific reason the thumb stops in the first 1.5 seconds>",
  "emotional_job": "<primary job>",
  "ai_feasibility": "<high|medium|low> - can Veo 3.1 build this without real footage of real people or real places?",
  "best_for": ["<which of cricket|bhakti|companionship this format serves, 1-3>"],
  "members": ["<exact format strings from the input that belong here>"]}}

Every input format must appear in exactly one cluster's members. JSON array only."""

    clusters = llm.ask_json(prompt, system=SYS)

    # attach real measured numbers to each cluster from its members
    by_fmt = {f["format"]: f for f in lb}
    for c in clusters:
        members = [by_fmt[m] for m in c.get("members", []) if m in by_fmt]
        c["measured"] = {
            "n_formats": len(members),
            "total_reach": sum(m["total_reach"] for m in members),
            "uses": sum(m["uses"] for m in members),
            "median_outlier": round(sorted([m["median_outlier"] for m in members])[len(members)//2], 1) if members else 0,
            "langs": sorted({l for m in members for l in m["langs"]}),
            "genres": sorted({g for m in members for g in m["genres"]}),
            "top_exemplar": max(members, key=lambda m: m["total_reach"])["exemplar"] if members else None,
        }
    clusters.sort(key=lambda c: -c["measured"]["total_reach"])

    t["canonical_formats"] = clusters
    json.dump(t, open(os.path.join(DATA, "trends", "trends.json"), "w"), indent=1, ensure_ascii=False)

    covered = sum(c["measured"]["n_formats"] for c in clusters)
    print(f"{len(clusters)} canonical formats covering {covered}/{len(lb)} labels\n")
    for c in clusters:
        m = c["measured"]
        print(f"{m['total_reach']:>10,}  out={m['median_outlier']:>7}  ai={c['ai_feasibility']:<6} "
              f"[{','.join(c['best_for'])[:26]:26}] {c['canonical']}")
        print(f"            {c['recipe'][:150]}")
        print(f"            HOOK: {c['hook_mechanic'][:130]}")
        print(f"            langs: {','.join(m['langs'])[:70]}\n")
    return clusters


if __name__ == "__main__":
    build(int(sys.argv[1]) if len(sys.argv) > 1 else 14)
