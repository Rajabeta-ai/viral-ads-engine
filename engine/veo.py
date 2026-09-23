"""Veo 3.1 text-to-video via Google Generative Language API (predictLongRunning + poll)."""
import os, time, json, base64, requests

BASE = "https://generativelanguage.googleapis.com/v1beta"

def _k():
    return os.environ["GOOGLE_API_KEY"]

def _h():
    return {"x-goog-api-key": _k(), "Content-Type": "application/json"}

def start(prompt, model=None, aspect="9:16", seconds=8, negative=None, resolution="720p"):
    model = model or os.environ.get("VEO_MODEL", "veo-3.1-fast-generate-preview")
    params = {"aspectRatio": aspect, "durationSeconds": seconds, "resolution": resolution}
    if negative:
        params["negativePrompt"] = negative
    r = requests.post(f"{BASE}/models/{model}:predictLongRunning", headers=_h(),
                      json={"instances": [{"prompt": prompt}], "parameters": params}, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"veo start {r.status_code}: {r.text[:500]}")
    return r.json()["name"]

def poll(op_name, timeout=900, every=10):
    t0 = time.time()
    while time.time() - t0 < timeout:
        r = requests.get(f"{BASE}/{op_name}", headers=_h(), timeout=60)
        if r.status_code != 200:
            raise RuntimeError(f"veo poll {r.status_code}: {r.text[:400]}")
        d = r.json()
        if d.get("done"):
            if "error" in d:
                raise RuntimeError(f"veo failed: {json.dumps(d['error'])[:500]}")
            return d
        time.sleep(every)
    raise TimeoutError(f"veo op {op_name} exceeded {timeout}s")

def _extract(d):
    """Veo responses nest the video under a few possible shapes."""
    resp = d.get("response", {})
    for key in ("generatedVideos", "generateVideoResponse", "predictions", "videos"):
        node = resp.get(key)
        if not node:
            continue
        if isinstance(node, dict):
            node = node.get("generatedSamples") or node.get("generatedVideos") or [node]
        for item in node:
            v = item.get("video") or item
            if v.get("uri"):
                return {"uri": v["uri"]}
            for b in ("bytesBase64Encoded", "videoBytes"):
                if v.get(b):
                    return {"b64": v[b]}
    raise RuntimeError(f"no video in response: {json.dumps(resp)[:600]}")

def download(d, out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    got = _extract(d)
    if "b64" in got:
        open(out_path, "wb").write(base64.b64decode(got["b64"]))
    else:
        r = requests.get(got["uri"], headers={"x-goog-api-key": _k()}, timeout=300)
        r.raise_for_status()
        open(out_path, "wb").write(r.content)
    return out_path

def generate(prompt, out_path, **kw):
    op = start(prompt, **kw)
    return download(poll(op), out_path)
