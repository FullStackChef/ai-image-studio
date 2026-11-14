#!/usr/bin/env python3
"""Download model weights onto a RunPod network volume. Run this once, not on every job."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

_here = Path(__file__).resolve().parent
sys.path.insert(0, str(_here))
sys.path.insert(0, str(_here.parent / "instance"))
import download_models


def main() -> int:
    volume = Path(os.environ.get("RUNPOD_VOLUME_PATH", "/runpod-volume"))
    manifest_path = Path(os.environ.get("MANIFEST", str(Path(__file__).with_name("models.manifest.json"))))
    if not manifest_path.is_file():
        manifest_path = Path("/opt/worker/models.manifest.json")
    if not volume.is_dir():
        print(f"Network volume is not mounted at {volume}", file=sys.stderr)
        return 1
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    token = download_models.normalize_token(os.environ.get("HF_TOKEN"))
    download_models.download_manifest(manifest, volume, lambda: token)
    print(f"Weights are in {volume / 'models'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
