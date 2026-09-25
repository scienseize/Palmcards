"""Download the MediaPipe .task model files into models/, and the Whisper model.

Run once after installing requirements:  python scripts/download_models.py
Files that already exist are skipped. Pass --force to re-download.

The Whisper model (palmcards/config.py SPEECH.model, ~1.6 GB) goes to the
Hugging Face cache (~/.cache/huggingface), where mlx-whisper looks for it.
"""

import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from palmcards.config import SPEECH  # noqa: E402

BASE = "https://storage.googleapis.com/mediapipe-models"

MODELS = {
    "hand_landmarker.task": f"{BASE}/hand_landmarker/hand_landmarker/float16/latest/hand_landmarker.task",
    "gesture_recognizer.task": f"{BASE}/gesture_recognizer/gesture_recognizer/float16/latest/gesture_recognizer.task",
    "face_landmarker.task": f"{BASE}/face_landmarker/face_landmarker/float16/latest/face_landmarker.task",
    "pose_landmarker_lite.task": f"{BASE}/pose_landmarker/pose_landmarker_lite/float16/latest/pose_landmarker_lite.task",
}

MODELS_DIR = Path(__file__).resolve().parent.parent / "models"


def download(name: str, url: str, force: bool) -> None:
    dest = MODELS_DIR / name
    if dest.exists() and dest.stat().st_size > 0 and not force:
        print(f"  skip  {name} (already present)")
        return
    part = dest.with_suffix(dest.suffix + ".part")
    print(f"  get   {name} ...", end="", flush=True)
    with urllib.request.urlopen(url, timeout=60) as resp, open(part, "wb") as out:
        while chunk := resp.read(1 << 16):
            out.write(chunk)
    part.rename(dest)
    print(f" {dest.stat().st_size / 1e6:.1f} MB")


def main() -> int:
    force = "--force" in sys.argv
    MODELS_DIR.mkdir(exist_ok=True)
    print(f"Downloading MediaPipe models into {MODELS_DIR}")
    failed = []
    for name, url in MODELS.items():
        try:
            download(name, url, force)
        except Exception as exc:  # keep going; report at the end
            print(f" FAILED: {exc}")
            failed.append(name)
    print(f"Fetching the Whisper model {SPEECH.model}")
    try:
        from huggingface_hub import snapshot_download

        print(f"  in    {snapshot_download(SPEECH.model, force_download=force)}")
    except Exception as exc:
        print(f"  FAILED: {exc}")
        failed.append(SPEECH.model)
    if failed:
        print(f"Failed: {', '.join(failed)}")
        return 1
    print("Done.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
