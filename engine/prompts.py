"""Generate the Veo 3.1 prompt library.

Every prompt is tagged with its ORIGIN so the three intelligence sources stay in balance:
  trend      - recreates a format measured as viral in the HikerAPI harvest
  insight    - original angle from strategic reasoning, deliberately NOT a copy
  competitor - answers something observed in the Meta Ad Library
The outro is intentionally omitted from every prompt: the team appends their own.
"""
import os, sys, json, itertools, datetime as dt
from concurrent.futures import ThreadPoolExecutor, as_completed
import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from engine import llm, guardrail

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
from engine.paths import DATA

SYS = """You are the best performance-creative director working in Indian vernacular direct response.
You write prompts for Veo 3.1 (text-to-video with NATIVE AUDIO - it can generate spoken dialogue,
ambience and music from the prompt).

Non-negotiables:
- The first 1.5 seconds must earn the scroll-stop on its own. No slow builds, no logo opens.
- Write for Bharat: tier-2/3 texture, real occupations, real rooms, real light. Not stock-India.
- Dialogue goes IN the prompt, in the target language, in Roman script with a native-script line
  beside it, spoken naturally - not announcer-voice.
- NEVER write an outro, end card, logo reveal, app screen, download CTA or final frame. The video
  ENDS on the emotional beat. The client's team appends their own outro. This is a hard rule.
- Veo prompts must be shootable: camera, lens feel, light, wardrobe, location, blocking, audio.
- No real brands, no real celebrities, no team jerseys, no deity faces animated as puppets.
- Depict adults only, clearly 25+.
"""


def load():
    trends = json.load(open(os.path.join(DATA, "trends", "trends.json")))
    comp = json.load(open(os.path.join(DATA, "trends", "competitors.json")))
    apps = yaml.safe_load(open(os.path.join(ROOT, "config", "apps.yaml")))["apps"]
    tax = yaml.safe_load(open(os.path.join(ROOT, "config", "taxonomy.yaml")))
    return trends, comp, apps, tax


def gen(app_key, app, lang_code, lang, states, origin, formats, comp, archetypes, n=2):
    ctx_formats = json.dumps(formats[:8], ensure_ascii=False)
    origin_brief = {
        "trend": "RECREATE one of the measured viral formats below for this app. Keep its structural "
                 "recipe and hook mechanic; change the subject to the app's job. Name which format you used.",
        "insight": "Do NOT copy the measured formats or the competitors. Invent an original angle that "
                   "exploits a gap neither is using. Justify the gap you are attacking.",
        "competitor": "Answer the competitive picture below - either by attacking the saturated hook "
                      "from an angle no competitor owns, or by out-executing a rail they all use.",
    }[origin]

    prompt = f"""APP: {app_key} - {app['one_liner']}
{('POSITIONING: ' + app['positioning']) if app.get('positioning') else ''}
DIFFERENTIATORS: {json.dumps(app.get('differentiators', []), ensure_ascii=False)}
HARD GUARDRAILS (violating these gets the ad rejected or misrepresents the product):
{json.dumps(app.get('guardrails', []), ensure_ascii=False)}

TARGET: {lang} speakers in {', '.join(states[:4])}.
AUDIENCE ARCHETYPES: {json.dumps(archetypes, ensure_ascii=False)}

MEASURED VIRAL FORMATS (from a live Instagram harvest, median_outlier = views/followers):
{ctx_formats}

COMPETITOR PICTURE (Meta Ad Library, live today):
saturated_hook: {comp['category_findings']['saturated_hook']}
structural_gap: {comp['category_findings']['structural_gap']}
winning_rails: {comp['category_findings']['winning_rails']}
length: {comp['category_findings']['length_sweet_spot']}

YOUR BRIEF ({origin}): {origin_brief}

Write {n} ad concepts. For each return:
{{"title": "<5-8 word concept name>",
  "origin": "{origin}",
  "based_on": "<the measured format name you recreated, or the gap/competitor rail you attacked>",
  "archetype": "<which audience archetype this targets>",
  "hook_line": "<the exact first spoken or on-screen line, in {lang}, Roman + native script>",
  "why_it_stops_the_scroll": "<one sentence, be specific about the first 1.5s>",
  "veo_prompt": "<the complete Veo 3.1 prompt: 8 seconds, vertical 9:16. Include shot, camera move, lens, lighting, location, wardrobe, casting age, blocking, the spoken dialogue in {lang} with native script, ambience and music direction. MUST NOT contain any outro, end card, logo, app UI or CTA - end on the emotional beat.>",
  "vo_script": "<optional separate voiceover line if the ad needs one over the Veo audio, in {lang}, else empty string>",
  "rationale": "<why this wins with this archetype in these states>",
  "risk_note": "<any Meta policy or brand-safety risk and how this creative avoids it>"}}

JSON array only."""
    return llm.ask_json(prompt, system=SYS)


def build(langs=("hi", "ta", "bn", "mr"), origins=("trend", "insight", "competitor"), per=2, workers=10):
    trends, comp, apps, tax = load()
    cf = trends.get("canonical_formats", [])
    formats = [{"format": c["canonical"], "recipe": c["recipe"], "hook": c["hook_mechanic"],
                "ai": c["ai_feasibility"], "median_outlier": c["measured"]["median_outlier"],
                "reach": c["measured"]["total_reach"], "best_for": c["best_for"]} for c in cf]

    jobs = []
    for app_key, app in apps.items():
        gfmt = [f for f in formats if app["genre"] in f["best_for"]] or formats
        arche = [a for a in tax["archetypes"].get(app["genre"], [])]
        for lc in langs:
            meta = tax["languages"][lc]
            for origin in origins:
                jobs.append((app_key, app, lc, meta["name"], meta["states"], origin, gfmt, arche))

    print(f"generating {len(jobs)} briefs x {per} concepts = {len(jobs)*per} prompts...")
    out = []

    def run(j):
        app_key, app, lc, lname, states, origin, gfmt, arche = j
        try:
            res = gen(app_key, app, lc, lname, states, origin, gfmt, comp, arche, per)
            for k, item in enumerate(res or []):
                item.update({"app": app_key, "genre": app["genre"], "lang": lc,
                             "language": lname, "states": states, "origin": origin,
                             "id": f"{app_key[:4]}-{lc}-{origin[:4]}-{k+1}"})
            return res or []
        except Exception as e:
            print(f"  ! {app_key}/{lc}/{origin}: {e}")
            return []

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = [ex.submit(run, j) for j in jobs]
        for i, f in enumerate(as_completed(futs), 1):
            r = f.result(); out.extend(r)
            print(f"  {i}/{len(jobs)} briefs -> {len(out)} prompts")

    # guardrail: flag anything that smuggled an outro in (negation-aware)
    for p in out:
        v = guardrail.violations(p.get("veo_prompt") or "")
        p["outro_clean"] = not v
        if v:
            p["outro_violations"] = v

    fp = os.path.join(DATA, "prompts", "prompts.json")
    os.makedirs(os.path.dirname(fp), exist_ok=True)
    # merge with anything already generated, newest wins on id collision
    prior = []
    if os.path.exists(fp):
        try:
            prior = json.load(open(fp)).get("prompts", [])
        except Exception:
            prior = []
    by_id = {p["id"]: p for p in prior}
    by_id.update({p["id"]: p for p in out})
    out = sorted(by_id.values(), key=lambda p: (p.get("app", ""), p.get("lang", ""), p.get("origin", "")))
    doc = {"generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
           "count": len(out),
           "mix": {o: sum(1 for p in out if p["origin"] == o)
                   for o in ("trend", "insight", "competitor")},
           "langs": sorted({p.get("lang") for p in out}),
           "prompts": out}
    json.dump(doc, open(fp, "w"), indent=1, ensure_ascii=False)
    dirty = [p["id"] for p in out if not p["outro_clean"]]
    print(f"\n{len(out)} prompts -> {fp}")
    print(f"mix: {doc['mix']}")
    print(f"outro-clean: {len(out)-len(dirty)}/{len(out)}" + (f"  DIRTY: {dirty}" if dirty else ""))
    return doc


if __name__ == "__main__":
    # usage: prompts.py [lang,lang,...|all] [per] [workers]
    import yaml as _y
    arg = sys.argv[1] if len(sys.argv) > 1 else "hi,ta,bn,mr"
    if arg in ("all", "*"):
        langs = tuple(_y.safe_load(open(os.path.join(ROOT, "config", "taxonomy.yaml")))["languages"])
    else:
        langs = tuple(x.strip() for x in arg.split(",") if x.strip())
    build(langs=langs,
          per=int(sys.argv[2]) if len(sys.argv) > 2 else 2,
          workers=int(sys.argv[3]) if len(sys.argv) > 3 else 10)
