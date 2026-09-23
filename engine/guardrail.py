"""Check that a Veo prompt contains no outro / end-card / CTA instruction.

Negated mentions ("no logos", "without an end card") are the CORRECT way to write these
prompts, so a naive substring scan false-positives on exactly the prompts that are right.
We only flag a term when it is not governed by a nearby negation.
"""
import re

BANNED = [r"end ?card", r"logo reveal", r"logo animation", r"brand reveal",
          r"download now", r"install now", r"app ?store", r"play ?store",
          r"call to action", r"\bcta\b", r"final frame shows", r"closing card",
          r"title card at the end", r"app (?:ui|screen|interface)", r"sign ?up screen"]

NEG = re.compile(
    r"\b(?:"
    r"no|not|none|never|without|avoid|omit|exclude|excluding|free of|absent|"
    r"do(?:es)?\s+not|did\s+not|must\s+not|should\s+not|shall\s+not|cannot|can'?t|"
    r"don'?t|doesn'?t|won'?t|refrain\s+from|keep\s+out|leave\s+out|no\s+such"
    r")\b")


def violations(text):
    """Return a list of (term, snippet) for banned terms that are NOT negated."""
    t = (text or "").lower()
    out = []
    for pat in BANNED:
        for m in re.finditer(pat, t):
            # A negation governs everything up to the end of its sentence, and these prompts
            # legitimately write long negative lists ("no logos, branded clothing, cta, outro,
            # end card"). So scope the search to the whole preceding sentence, not a fixed window.
            sentence = re.split(r"(?<=[.!?])\s+", t[:m.start()])[-1]
            if NEG.search(sentence):
                continue
            out.append((m.group(0), t[max(0, m.start() - 40):m.end() + 25].strip()))
    return out


def is_clean(text):
    return not violations(text)
