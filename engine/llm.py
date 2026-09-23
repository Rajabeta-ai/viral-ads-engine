"""Azure gpt-6-astra (Responses API) client used for classification + prompt drafting."""
import os, json, time, requests

def _cfg():
    return (os.environ["AZURE_ENDPOINT"], os.environ["AZURE_API_KEY"],
            os.environ.get("AZURE_MODEL", "gpt-6-astra"))

def ask(prompt, system=None, json_mode=False, max_retries=3, timeout=300):
    ep, key, model = _cfg()
    inp = []
    if system:
        inp.append({"role": "system", "content": system})
    inp.append({"role": "user", "content": prompt})
    body = {"model": model, "input": inp}
    last = None
    for a in range(max_retries):
        try:
            r = requests.post(ep, headers={"Content-Type": "application/json", "api-key": key},
                              json=body, timeout=timeout)
            if r.status_code == 200:
                return _text(r.json())
            last = f"HTTP {r.status_code}: {r.text[:300]}"
        except Exception as e:
            last = repr(e)
        time.sleep(2 * (a + 1))
    raise RuntimeError(f"azure failed: {last}")

def _text(d):
    if isinstance(d.get("output_text"), str) and d["output_text"].strip():
        return d["output_text"]
    parts = []
    for item in d.get("output", []) or []:
        for c in item.get("content", []) or []:
            if c.get("type") in ("output_text", "text") and c.get("text"):
                parts.append(c["text"])
    return "\n".join(parts).strip()

def ask_json(prompt, system=None, **kw):
    """Ask for JSON and parse defensively (strips fences / prose)."""
    raw = ask(prompt + "\n\nRespond with ONLY valid JSON. No markdown fences, no commentary.",
              system=system, **kw)
    t = raw.strip()
    if t.startswith("```"):
        t = t.split("```")[1]
        t = t[4:].lstrip() if t.lower().startswith("json") else t
    for lo, hi in (("{", "}"), ("[", "]")):
        i, j = t.find(lo), t.rfind(hi)
        if i != -1 and j > i:
            try:
                return json.loads(t[i:j + 1])
            except Exception:
                continue
    raise ValueError(f"unparseable JSON from model: {raw[:400]}")
