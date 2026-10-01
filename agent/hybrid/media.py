"""Decode actual owned media in a bounded isolated PyAV child process."""
import json
import math
import subprocess
import sys
from pathlib import Path


class MediaError(ValueError):
    pass


# Static code only. BytesIO custom AVIO, external protocols disabled, no URLs.
PYAV_PROBE = r'''
import av, io, json, pathlib, sys
p = pathlib.Path(sys.argv[1])
if p.stat().st_size > 268435456: raise ValueError("Media too large")
with av.open(io.BytesIO(p.read_bytes()), mode="r", options={"protocol_whitelist":"pipe", "enable_drefs":"0", "use_absolute_path":"0"}) as c:
    allowed = {"mov", "mp4", "m4a", "3gp", "3g2", "mj2", "matroska", "webm", "avi", "png_pipe", "jpeg_pipe", "webp_pipe"}
    if not set(c.format.name.split(",")).intersection(allowed): raise ValueError("Unsupported local media format")
    videos = list(c.streams.video)
    if len(videos) != 1: raise ValueError("One visual stream required")
    s = videos[0]
    if not 0 < s.width <= 8192 or not 0 < s.height <= 8192 or s.width*s.height > 16777216:
        raise ValueError("Predecode dimension limit")
    rate = float(s.average_rate or 0)
    count, end, width, height = 0, 0.0, None, None
    image = c.format.name in {"png_pipe", "jpeg_pipe", "webp_pipe"}
    for f in c.decode(s):
        count += 1
        if count > 72000 or image and count > 1: raise ValueError("Decode frame limit")
        if width is None: width, height = f.width, f.height
        if (f.width, f.height) != (width, height): raise ValueError("Variable dimensions")
        if f.pts is not None and f.time_base is not None:
            step = float(f.duration * f.time_base) if f.duration else 1/rate if rate > 0 else 0
            end = max(end, float(f.pts * f.time_base) + step)
    if count == 0: raise ValueError("No decodable visual bytes")
    if image: end, rate = 0.0, 0.0
    if not image and (end <= 0 or rate <= 0): raise ValueError("Video timing required")
    print(json.dumps({"width":width,"height":height,"durationSeconds":end,"fps":rate,"hasAudio":bool(c.streams.audio),"decodedFrames":count,"probe":"pyav-owned-bytes"}))
'''


def probe_media(path: Path, runtime_root: Path) -> dict:
    if not path.resolve().is_relative_to(runtime_root.resolve()) or not path.is_file():
        raise MediaError("Only owned runtime media may be inspected")
    try:
        result = subprocess.run([sys.executable, "-I", "-c", PYAV_PROBE, str(path)], capture_output=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise MediaError("Local media decode could not complete") from exc
    if result.returncode != 0 or len(result.stdout) > 65536:
        raise MediaError("Media decode rejected these bytes")
    try:
        data = json.loads(result.stdout)
        if not all(math.isfinite(data[key]) for key in ("durationSeconds", "fps")) or data["width"] <= 0 or data["height"] <= 0:
            raise ValueError("Invalid decoded metadata")
        return data
    except (ValueError, KeyError, TypeError) as exc:
        raise MediaError("Media decode returned invalid metadata") from exc


def inspect_input(path: Path, role: str, runtime_root: Path) -> dict:
    with path.open("rb") as stream:
        header = stream.read(16)
    if role != "SOURCE_MOTION" and not (header.startswith(b"\x89PNG\r\n\x1a\n") or header.startswith(b"\xff\xd8\xff") or header.startswith(b"RIFF") and header[8:12] == b"WEBP"):
        raise MediaError("Image role requires PNG, JPEG or WebP bytes")
    meta = probe_media(path, runtime_root)
    if role == "SOURCE_MOTION" and (meta["durationSeconds"] <= 0 or meta["fps"] <= 0):
        raise MediaError("Source motion requires an actual video")
    if role != "SOURCE_MOTION" and (meta["decodedFrames"] != 1 or meta["durationSeconds"] != 0):
        raise MediaError("Before/After/product image must be a single still image")
    return meta


def validate_output(meta: dict, spec: dict):
    width, height = map(int, spec["resolution"].split("x"))
    if (meta["width"], meta["height"]) != (width, height):
        raise MediaError("Output dimensions differ from authorized resolution")
    if meta["hasAudio"] or meta["fps"] <= 0 or meta["durationSeconds"] <= 0:
        raise MediaError("Output must be a real silent video with positive duration and FPS")
    if abs(meta["durationSeconds"] - spec["durationSeconds"]) > max(0.25, 1 / meta["fps"]):
        raise MediaError("Output duration differs from authorized duration")
