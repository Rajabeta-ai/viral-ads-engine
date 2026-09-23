"""Classify harvested bhakti reels against the Prabhuji archetype taxonomy.

Each reel gets: which archetype it would serve, which specific angle, which app feature
it sells (ringtone / wallpaper / mantra / status), and which emotion fires.

The vocabularies are CLOSED. A model that invents an archetype or an emotion makes the UI
columns meaningless, so anything outside the config is rejected and left unset rather than
silently accepted.
"""
import os, sys, json, collections
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import llm
from engine.paths import DATA, CONFIG

FP = os.path.join(DATA, "scored", "reels.json")

SYS = ("You are a Hindu devotional-marketing strategist for Prabhuji, an AI Sanatan app that "
       "sells four things: ringtones, wallpapers, mantras and WhatsApp statuses. You read a "
       "viral bhakti reel and judge which customer it would convert and how. You are literal "
       "and evidence-led: you only use the caption, hashtags, audio and metrics you are given, "
       "and you say so when the evidence is thin.")


def load_tax():
    return yaml.safe_load(open(os.path.join(CONFIG, "prabhuji.yaml")))


def _brief(tax):
    lines = []
    for a in tax["archetypes"]:
        lines.append(f"{a['id']} ({a['n']}. {a['label']}) - {a['psychology']} Buys: {a['buying']} "
                     f"Features: {','.join(a['features'])} Fit: {a['fit']}")
        for g in a["angles"]:
            lines.append(f"    {g['id']}: {g['hook']} | trigger={g['trigger']} | sells={','.join(g['sells'])}")
    return "\n".join(lines)


def classify_batch(reels, tax):
    payload = [{"i": i, "lang": r["lang"], "date": r["date"], "reach": r["reach"],
                "followers": r.get("followers"), "outlier": r.get("outlier"),
                "audio": (r.get("audio") or {}).get("title"),
                "format": r.get("format"), "caption": (r.get("caption") or "")[:340]}
               for i, r in enumerate(reels)]

    prompt = f"""PRABHUJI ARCHETYPES AND ANGLES:
{_brief(tax)}

VALID features: {tax['features']}
VALID emotional_trigger values (closed list, pick exactly one): {tax['emotional_triggers']}

Here are {len(reels)} viral bhakti Reels harvested from Indian Instagram:
{json.dumps(payload, ensure_ascii=False)}

For each reel, decide which Prabhuji customer it would actually convert.

Return a JSON array of:
{{"i": <index>,
  "archetype": "<one archetype id from the list above>",
  "angle": "<one angle id, e.g. 1A, 5B - must belong to the archetype you chose>",
  "feature": "<the ONE feature this reel most obviously sells: ringtone|wallpaper|mantra|status>",
  "emotional_trigger": "<exactly one value from the closed list above>",
  "secondary_trigger": "<another value from the closed list, or empty string>",
  "confidence": "<high|medium|low> - low when you are inferring from hashtags alone",
  "why": "<one short sentence tying the reel's actual content to that archetype>"}}

Judge on the evidence given. If a reel is aggressive Mahakal/Bajrangbali energy, say
aggression. If it is Sanatani/regional assertion, say pride_identity. If it is simple
reverence, say religion. Do not default everything to religion.

JSON array only."""
    return llm.ask_json(prompt, system=SYS)


def build(limit=260, batch=26, workers=5, only_classified=True):
    tax = load_tax()
    valid_arch = {a["id"]: a for a in tax["archetypes"]}
    valid_angle = {g["id"]: a["id"] for a in tax["archetypes"] for g in a["angles"]}
    valid_feat = set(tax["features"])
    valid_trig = set(tax["emotional_triggers"])

    d = json.load(open(FP))
    rows = d["rows"]
    pool = [r for r in rows if r.get("genre") == "bhakti" and r.get("enriched")]
    if only_classified:
        pool = [r for r in pool if r.get("format")] or pool
    pool.sort(key=lambda r: -r.get("score", 0))
    pool = pool[:limit]
    print(f"classifying {len(pool)} bhakti reels against {len(valid_arch)} archetypes "
          f"/ {len(valid_angle)} angles...")

    chunks = [pool[i:i + batch] for i in range(0, len(pool), batch)]

    def run(ic):
        i, c = ic
        try:
            return i, classify_batch(c, tax)
        except Exception as e:
            print(f"  ! batch {i}: {e}")
            return i, []

    results, rejected = {}, collections.Counter()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for f in as_completed([ex.submit(run, (i, c)) for i, c in enumerate(chunks)]):
            i, res = f.result()
            results[i] = res
            print(f"  batch {i+1}/{len(chunks)} -> {len(res)}")

    applied = 0
    for i, res in results.items():
        base = i * batch
        for item in res or []:
            try:
                r = pool[base + int(item["i"])]
            except (KeyError, ValueError, IndexError):
                continue
            arch, ang = item.get("archetype"), item.get("angle")
            feat, trig = item.get("feature"), item.get("emotional_trigger")
            # closed-vocabulary enforcement
            if arch not in valid_arch:
                rejected["archetype"] += 1; continue
            if ang not in valid_angle or valid_angle[ang] != arch:
                rejected["angle"] += 1; ang = None
            if feat not in valid_feat:
                rejected["feature"] += 1; feat = None
            if trig not in valid_trig:
                rejected["trigger"] += 1; trig = None
            sec = item.get("secondary_trigger")
            r["pj_archetype"] = arch
            r["pj_archetype_label"] = valid_arch[arch]["label"]
            r["pj_angle"] = ang
            r["pj_angle_hook"] = next((g["hook"] for g in valid_arch[arch]["angles"]
                                       if g["id"] == ang), None)
            r["pj_feature"] = feat
            r["pj_trigger"] = trig
            r["pj_trigger2"] = sec if sec in valid_trig else None
            r["pj_confidence"] = item.get("confidence")
            r["pj_why"] = item.get("why")
            applied += 1

    d["rows"] = rows
    d["prabhuji_classified"] = applied
    json.dump(d, open(FP, "w"), indent=1, ensure_ascii=False)
    print(f"\napplied to {applied} reels; rejected {dict(rejected) or 'nothing'}")

    print("\nARCHETYPE DISTRIBUTION:")
    for k, n in collections.Counter(r["pj_archetype_label"] for r in rows
                                    if r.get("pj_archetype_label")).most_common():
        print(f"  {n:>4}  {k}")
    print("\nEMOTIONAL TRIGGERS:")
    for k, n in collections.Counter(r["pj_trigger"] for r in rows if r.get("pj_trigger")).most_common():
        print(f"  {n:>4}  {k}")
    print("\nFEATURE PULL:")
    for k, n in collections.Counter(r["pj_feature"] for r in rows if r.get("pj_feature")).most_common():
        print(f"  {n:>4}  {k}")
    return applied


if __name__ == "__main__":
    build(limit=int(sys.argv[1]) if len(sys.argv) > 1 else 260)
