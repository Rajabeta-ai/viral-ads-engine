#!/usr/bin/env python3
"""Run the whole pipeline end to end.  python3 run.py [--from stage] [--skip-render]"""
import os, sys, subprocess, argparse, time

ROOT = os.path.dirname(os.path.abspath(__file__))
ORDER = ["harvest", "enrich", "trends", "cluster", "archetypes", "prompts", "render"]
ARGS = {"harvest": ["45"], "enrich": ["600", "40"], "trends": ["240", "15"], "cluster": ["14"], "archetypes": ["260"],
        "prompts": ["all", "2", "10"], "render": ["--limit", "3"]}


def load_env():
    f = os.path.join(ROOT, ".env")
    if os.path.exists(f):
        for line in open(f):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start", default="harvest", choices=ORDER)
    ap.add_argument("--only", nargs="*", choices=ORDER)
    ap.add_argument("--skip-render", action="store_true")
    a = ap.parse_args()
    load_env()

    stages = a.only or ORDER[ORDER.index(a.start):]
    if a.skip_render and "render" in stages:
        stages = [s for s in stages if s != "render"]

    for s in stages:
        print(f"\n{'='*64}\n== {s.upper()}\n{'='*64}")
        t0 = time.time()
        rc = subprocess.call([sys.executable, os.path.join(ROOT, "engine", f"{s}.py")] + ARGS[s],
                             cwd=ROOT, env=os.environ)
        print(f"-- {s} exited {rc} in {time.time()-t0:.0f}s")
        if rc != 0:
            print(f"!! pipeline stopped at {s}")
            return rc
    print("\nall stages complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
