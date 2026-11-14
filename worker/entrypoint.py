#!/usr/bin/env python3
"""Run one ComfyUI workflow, then exit so Batch can terminate the machine."""

from __future__ import annotations

import json
import os
import subprocess
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def missing_required(comfy_root: Path, manifest: dict) -> list[str]:
    missing = []
    for model in manifest["models"]:
        if not model.get("required"):
            continue
        path = comfy_root / model["dest"]
        if not path.is_file() or path.stat().st_size < int(model["min_bytes"]):
            missing.append(model["id"])
    return missing


def sync_command(source: str, dest: str) -> list[str]:
    return ["aws", "s3", "sync", source, dest, "--only-show-errors"]


def run(command: list[str]) -> None:
    completed = subprocess.run(command, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"command failed ({completed.returncode}): {' '.join(command[:4])}")


def sync_weights(bucket: str, comfy_root: Path, manifest: dict) -> None:
    run(sync_command(f"s3://{bucket}/weights/", str(comfy_root)))
    if not missing_required(comfy_root, manifest):
        return
    import download_models

    region = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "ap-southeast-2"
    download_models.download_manifest(manifest, comfy_root, lambda: download_models.fetch_token(region))
    if missing_required(comfy_root, manifest):
        raise SystemExit("required weights are still missing after the Hugging Face download")
    run(sync_command(str(comfy_root / "models"), f"s3://{bucket}/weights/models/"))


def start_comfy(comfy_root: Path) -> subprocess.Popen[bytes]:
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


def wait_for_comfy(timeout: int = 600) -> None:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:8188/system_stats", timeout=5) as response:
                if response.status == 200:
                    return
        except (urllib.error.URLError, TimeoutError, ConnectionError):
            time.sleep(3)
    raise SystemExit("ComfyUI did not become ready")


def post_json(url: str, payload: dict) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=120) as response:
        return json.loads(response.read().decode())


def wait_for_image(prompt_id: str, save_node: str, timeout: int = 1200) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        with urllib.request.urlopen(f"http://127.0.0.1:8188/history/{prompt_id}", timeout=30) as response:
            history = json.loads(response.read().decode())
        entry = history.get(prompt_id)
        if isinstance(entry, dict):
            status = entry.get("status") or {}
            if status.get("status_str") == "error":
                raise SystemExit(f"ComfyUI failed: {status.get('messages')}")
            images = ((entry.get("outputs") or {}).get(save_node) or {}).get("images") or []
            if images:
                return images[0]
            if status.get("completed"):
                raise SystemExit("ComfyUI finished without an image")
        time.sleep(2)
    raise SystemExit("timed out waiting for the image")


def download_image(image: dict, dest: Path) -> None:
    query = urllib.parse.urlencode(
        {
            "filename": image["filename"],
            "subfolder": image.get("subfolder") or "",
            "type": image.get("type") or "output",
        }
    )
    with urllib.request.urlopen(f"http://127.0.0.1:8188/view?{query}", timeout=120) as response:
        dest.write_bytes(response.read())


def main() -> None:
    bucket = os.environ["JOB_BUCKET"]
    job_id = os.environ["JOB_ID"]
    comfy_root = Path(os.environ.get("COMFY_ROOT", "/opt/ComfyUI"))
    manifest = json.loads(Path(os.environ.get("MANIFEST", "/opt/worker/models.manifest.json")).read_text(encoding="utf-8"))
    work = Path("/tmp/ai-image-studio")
    work.mkdir(parents=True, exist_ok=True)
    (comfy_root / "input").mkdir(parents=True, exist_ok=True)
    (comfy_root / "output").mkdir(parents=True, exist_ok=True)

    sync_weights(bucket, comfy_root, manifest)
    request_path = work / "request.json"
    run(["aws", "s3", "cp", f"s3://{bucket}/jobs/{job_id}/request.json", str(request_path)])
    request = json.loads(request_path.read_text(encoding="utf-8"))
    for item in request.get("files") or []:
        target = comfy_root / "input" / item["name"]
        run(["aws", "s3", "cp", f"s3://{bucket}/{item['key']}", str(target)])

    process = start_comfy(comfy_root)
    try:
        wait_for_comfy()
        submitted = post_json("http://127.0.0.1:8188/prompt", {"prompt": request["workflow"], "client_id": job_id})
        if submitted.get("node_errors") or submitted.get("error") or not submitted.get("prompt_id"):
            raise SystemExit(f"ComfyUI rejected the workflow: {submitted}")
        image = wait_for_image(submitted["prompt_id"], request.get("save_node", "90"))
        local = work / "result.png"
        download_image(image, local)
        output_key = f"outputs/{job_id}.png"
        run(["aws", "s3", "cp", str(local), f"s3://{bucket}/{output_key}", "--sse", "AES256"])
        result = {"job_id": job_id, "output_key": output_key, "mode": request.get("mode")}
        result_path = work / "result.json"
        result_path.write_text(json.dumps(result) + "\n", encoding="utf-8")
        run(["aws", "s3", "cp", str(result_path), f"s3://{bucket}/jobs/{job_id}/result.json", "--sse", "AES256"])
    finally:
        if process.poll() is None:
            process.terminate()


if __name__ == "__main__":
    main()
