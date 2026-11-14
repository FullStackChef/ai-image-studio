#!/usr/bin/env python3
"""RunPod serverless handler. One ComfyUI workflow, then the worker can scale to zero.

Model weights are read from a network volume mounted at /runpod-volume.
They are not stored in this image.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

VOLUME_ENV = "RUNPOD_VOLUME_PATH"
DEFAULT_VOLUME = "/runpod-volume"


def volume_path() -> Path:
    return Path(os.environ.get(VOLUME_ENV, DEFAULT_VOLUME))


def job_input(event: dict) -> dict:
    if not isinstance(event, dict):
        raise ValueError("RunPod event must be an object")
    data = event.get("input")
    if not isinstance(data, dict):
        raise ValueError("RunPod event is missing input")
    for key in ("bucket", "job_id"):
        value = data.get(key)
        if not isinstance(value, str) or not value or "/" in value or ".." in value:
            raise ValueError(f"input.{key} must be a plain string")
    return data


def link_models(volume: Path, comfy_root: Path) -> None:
    source = volume / "models"
    if not source.is_dir() or not any(source.iterdir()):
        raise FileNotFoundError(
            f"No model weights in {source}. Attach a RunPod network volume and run seed_weights.py once. "
            "Weights are not baked into the image."
        )
    dest = comfy_root / "models"
    if dest.is_symlink():
        if dest.resolve() == source.resolve():
            return
        dest.unlink()
    elif dest.exists():
        shutil.rmtree(dest)
    dest.symlink_to(source, target_is_directory=True)


def _run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise RuntimeError(f"command failed ({completed.returncode}): {' '.join(command[:4])}")


def _download_request(bucket: str, job_id: str, work: Path) -> dict:
    path = work / "request.json"
    _run(["aws", "s3", "cp", f"s3://{bucket}/jobs/{job_id}/request.json", str(path)])
    return json.loads(path.read_text(encoding="utf-8"))


def _start_comfy(comfy_root: Path) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        [
            "python",
            "main.py",
            "--listen",
            "127.0.0.1",
            "--port",
            "8188",
            "--output-directory",
            str(comfy_root / "output"),
            "--input-directory",
            str(comfy_root / "input"),
            "--feature-flag",
            "local_web_sso=false",
        ],
        cwd=comfy_root,
    )


def _wait_for_comfy(timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3)
    raise RuntimeError("ComfyUI did not become ready")


def _wait_for_image(prompt_id: str, save_node: str, timeout: int = 1200) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"http://127.0.0.1:8188/history/{prompt_id}", timeout=30) as response:
            history = json.loads(response.read().decode())
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise RuntimeError(f"ComfyUI failed: {status.get('messages')}")
            images = ((entry.get("outputs") or {}).get(save_node) or {}).get("images") or []
            if images:
                return images[0]
            if status.get("completed"):
                raise RuntimeError("ComfyUI finished without an image")
        time.sleep(2)
    raise RuntimeError("timed out waiting for the image")


def process(event: dict, comfy_root: Path | None = None, volume: Path | None = None) -> dict:
    data = job_input(event)
    bucket = data["bucket"]
    job_id = data["job_id"]
    root = comfy_root or Path(os.environ.get("COMFY_ROOT", "/opt/ComfyUI"))
    link_models(volume or volume_path(), root)
    work = Path("/tmp/ai-image-studio")
    work.mkdir(parents=True, exist_ok=True)
    (root / "input").mkdir(parents=True, exist_ok=True)
    (root / "output").mkdir(parents=True, exist_ok=True)
    request = _download_request(bucket, job_id, work)
    for item in request.get("files") or []:
        name = item["name"]
        if "/" in name or name.startswith("."):
            raise ValueError(f"refusing input name {name}")
        _run(["aws", "s3", "cp", f"s3://{bucket}/{item['key']}", str(root / "input" / name)])

    comfy = _start_comfy(root)
    try:
        _wait_for_comfy()
        body = json.dumps({"prompt": request["workflow"], "client_id": job_id}).encode()
        call = urllib.request.Request("http://127.0.0.1:8188/prompt", data=body, method="POST")
        call.add_header("Content-Type", "application/json")
        with urllib.request.urlopen(call, timeout=120) as response:
            submitted = json.loads(response.read().decode())
        if submitted.get("node_errors") or submitted.get("error") or not submitted.get("prompt_id"):
            raise RuntimeError(f"ComfyUI rejected the workflow: {submitted}")
        image = _wait_for_image(submitted["prompt_id"], request.get("save_node", "90"))
        query = urllib.parse.urlencode(
            {
                "filename": image["filename"],
                "subfolder": image.get("subfolder") or "",
                "type": image.get("type") or "output",
            }
        )
        local = work / "result.png"
        with urllib.request.urlopen(f"http://127.0.0.1:8188/view?{query}", timeout=120) as response:
            local.write_bytes(response.read())
        output_key = f"outputs/{job_id}.png"
        _run(["aws", "s3", "cp", str(local), f"s3://{bucket}/{output_key}", "--sse", "AES256"])
        return {"bucket": bucket, "job_id": job_id, "output_key": output_key}
    finally:
        if comfy.poll() is None:
            comfy.terminate()


def handler(event: dict) -> dict:
    return process(event)


if __name__ == "__main__":
    import runpod

    runpod.serverless.start({"handler": handler})
