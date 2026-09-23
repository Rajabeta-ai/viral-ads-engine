"""ffmpeg assembly: normalise clips, concat scenes, mix VO, attach the team's outro."""
import os, subprocess, json, shutil

W, H, FPS, AR = 1080, 1920, 30, 48000

def _run(cmd):
    p = subprocess.run(cmd, capture_output=True, text=True)
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg failed:\n{' '.join(cmd[:14])}...\n{p.stderr[-1400:]}")
    return p

def probe(path):
    p = subprocess.run(["ffprobe", "-v", "quiet", "-print_format", "json", "-show_format",
                        "-show_streams", path], capture_output=True, text=True)
    return json.loads(p.stdout or "{}")

def normalize(src, dst):
    """Force every clip to the same codec/size/fps/audio so concat never re-encodes badly."""
    has_audio = any(s.get("codec_type") == "audio" for s in probe(src).get("streams", []))
    cmd = ["ffmpeg", "-y"]
    if not has_audio:
        cmd += ["-f", "lavfi", "-t", "0.1", "-i", f"anullsrc=r={AR}:cl=stereo"]
    cmd = ["ffmpeg", "-y", "-i", src]
    if not has_audio:
        cmd += ["-f", "lavfi", "-i", f"anullsrc=r={AR}:cl=stereo", "-shortest"]
    cmd += ["-vf", f"scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},fps={FPS},setsar=1",
            "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2", dst]
    _run(cmd)
    return dst

def add_voiceover(video, vo_mp3, dst, vo_gain=1.0, bed_gain=0.25):
    """Duck the source audio under the VO track."""
    _run(["ffmpeg", "-y", "-i", video, "-i", vo_mp3, "-filter_complex",
          f"[0:a]volume={bed_gain}[bed];[1:a]volume={vo_gain}[vo];"
          f"[bed][vo]amix=inputs=2:duration=first:dropout_transition=0[a]",
          "-map", "0:v", "-map", "[a]", "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", dst])
    return dst

def concat(parts, dst, workdir=None):
    """Normalise then concat. Returns dst."""
    workdir = workdir or os.path.join(os.path.dirname(dst), "_work")
    os.makedirs(workdir, exist_ok=True)
    normed = []
    for i, p in enumerate(parts):
        n = os.path.join(workdir, f"n{i:02d}.mp4")
        normalize(p, n)
        normed.append(n)
    lst = os.path.join(workdir, "concat.txt")
    with open(lst, "w") as f:
        for n in normed:
            f.write(f"file '{os.path.abspath(n)}'\n")
    _run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
          "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p",
          "-c:a", "aac", "-b:a", "192k", "-ar", str(AR), "-ac", "2", dst])
    shutil.rmtree(workdir, ignore_errors=True)
    return dst

def attach_outro(body, outro, dst):
    """The one the team cares about: body + the team-supplied outro, seamlessly."""
    if not os.path.exists(outro):
        raise FileNotFoundError(f"outro not found: {outro}")
    return concat([body, outro], dst)

def burn_captions(video, srt, dst, fontsize=54):
    style = (f"FontName=Arial,FontSize={fontsize},PrimaryColour=&H00FFFFFF,"
             f"OutlineColour=&H00000000,BorderStyle=1,Outline=3,Shadow=1,"
             f"Alignment=2,MarginV=260")
    _run(["ffmpeg", "-y", "-i", video, "-vf",
          f"subtitles={srt}:force_style='{style}'", "-c:a", "copy", dst])
    return dst
