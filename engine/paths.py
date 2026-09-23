"""Single source of truth for where data lives.

Locally that is ./data. On Render it must be the mounted persistent disk, because a
container's own filesystem is wiped on every deploy and restart - so DATA_DIR overrides it.
"""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA = os.environ.get("DATA_DIR") or os.path.join(ROOT, "data")
CONFIG = os.path.join(ROOT, "config")

for sub in ("raw", "scored", "trends", "prompts", "renders", "assets"):
    os.makedirs(os.path.join(DATA, sub), exist_ok=True)


def d(*parts):
    return os.path.join(DATA, *parts)
