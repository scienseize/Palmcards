"""Download the models PalmCards needs, pinned and checked.

Run once after installing requirements:  python scripts/download_models.py [--all] [--force]

Required today:
  models/gesture_recognizer.task   MediaPipe hands and gestures
  models/face_landmarker.task      face and eyes during takes (milestone 7; without it,
  models/pose_landmarker_lite.task and shoulders, takes are recorded without them)
  SPEECH.model, SPEECH.live_model  Whisper for each take (~1.6 GB) and for
                                   following the voice live (small), at the
                                   revisions pinned in palmcards/config.py,
                                   into the Hugging Face cache
                                   (~/.cache/huggingface) where mlx-whisper
                                   looks for them
Optional (--all): the MediaPipe hand landmarker (the gesture recognizer
already includes it).

MediaPipe files come from versioned URLs and are checked against their
SHA-256; a file on disk that doesn't match is reported (and replaced with
--force). Files that already match are skipped.
"""

import hashlib
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palmcards.config import SPEECH  # noqa: E402

BASE = "https://storage.googleapis.com/mediapipe-models"
# name: (url, sha256, required today)
MODELS = {
    "gesture_recognizer.task": (f"{BASE}/gesture_recognizer/gesture_recognizer/float16/1/gesture_recognizer.task",
                                "97952348cf6a6a4915c2ea1496b4b37ebabc50cbbf80571435643c455f2b0482", True),
    "hand_landmarker.task": (f"{BASE}/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task",
                             "fbc2a30080c3c557093b5ddfc334698132eb341044ccee322ccf8bcf3607cde1", False),
    "face_landmarker.task": (f"{BASE}/face_landmarker/face_landmarker/float16/1/face_landmarker.task",
                             "64184e229b263107bc2b804c6625db1341ff2bb731874b0bcc2fe6544e0bc9ff", True),
    "pose_landmarker_lite.task": (f"{BASE}/pose_landmarker/pose_landmarker_lite/float16/1/pose_landmarker_lite.task",
                                  "59929e1d1ee95287735ddd833b19cf4ac46d29bc7afddbbf6753c459690d574a", True),
}
WHISPER = {SPEECH.model: SPEECH.model_revision, SPEECH.live_model: SPEECH.live_model_revision}

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


def download(name: str, url: str, digest: str, force: bool) -> None:
    dest = MODELS_DIR / name
    if dest.exists() and not force:
        if sha256(dest) == digest:
            print(f"  ok    {name}")
            return
        raise RuntimeError(f"{name} on disk does not match its pinned SHA-256; run with --force to replace it")
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"  get   {name} ...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
        while chunk := resp.read(1 << 16):
            out.write(chunk)
    if sha256(part) != digest:
        part.unlink()
        raise RuntimeError(f"{name} downloaded but its SHA-256 does not match the pinned one; not installed")
    part.rename(dest)
    print(f" {dest.stat().st_size / 1e6:.1f} MB, checksum ok")


def main() -> int:
    force, everything = "--force" in sys.argv, "--all" in sys.argv
    MODELS_DIR.mkdir(exist_ok=True)
    print(f"MediaPipe models in {MODELS_DIR}")
    failed = []
    for name, (url, digest, required) in MODELS.items():
        if not (required or everything):
            print(f"  skip  {name} (optional; --all to fetch)")
            continue
        try:
            download(name, url, digest, force)
        except Exception as exc:  # keep going; report at the end
            print(f"  FAILED: {exc}")
            failed.append(name)
    for repo, revision in WHISPER.items():
        print(f"Whisper model {repo} @ {(revision or 'latest')[:12]}")
        try:
            from huggingface_hub import snapshot_download

            print(f"  in    {snapshot_download(repo, revision=revision, force_download=force)}")
        except Exception as exc:
            print(f"  FAILED: {exc}")
            failed.append(repo)
    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
