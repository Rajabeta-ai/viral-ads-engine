"""ElevenLabs TTS. The supplied key is TTS-scoped (cannot list voices), so IDs are pinned."""
import os, requests

# Stock public voice IDs - key lacks voices_read, so these are hardcoded.
VOICES = {
    "female_warm":   "21m00Tcm4TlvDq8ikWAM",  # Rachel
    "female_soft":   "EXAVITQu4vr4xnSDxMaL",  # Bella
    "male_deep":     "pNInz6obpgDQGcFmaJgB",  # Adam
    "male_narrator": "onwK4e9ZLuTAKqWW03F9",  # Daniel
}

def tts(text, out_path, voice="female_warm", model="eleven_multilingual_v2",
        stability=0.45, similarity=0.8, style=0.35):
    vid = VOICES.get(voice, voice)
    r = requests.post(
        f"https://api.elevenlabs.io/v1/text-to-speech/{vid}",
        headers={"xi-api-key": os.environ["ELEVENLABS_API_KEY"], "Content-Type": "application/json"},
        json={"text": text, "model_id": model,
              "voice_settings": {"stability": stability, "similarity_boost": similarity,
                                 "style": style, "use_speaker_boost": True}},
        timeout=180)
    if r.status_code != 200:
        raise RuntimeError(f"elevenlabs {r.status_code}: {r.text[:300]}")
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    open(out_path, "wb").write(r.content)
    return out_path
