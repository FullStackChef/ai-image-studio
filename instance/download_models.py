#!/usr/bin/env python3
"""Download the model manifest onto the ComfyUI data disk.

Required files must land completely. Optional files (the gated SDXL
checkpoint, for example) log a warning and leave the rest of the studio
usable. A missing Hugging Face token is retried for required files so
deploy.sh can store the token while first boot is still running.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import time
from pathlib import Path
from typing import Callable, Iterable, Sequence

TOKEN_PLACEHOLDERS = {"", "unset", "none", "null"}
AUTH_WAIT_SECONDS = 600
AUTH_POLL_SECONDS = 30


def normalize_token(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if value.lower() in TOKEN_PLACEHOLDERS:
        return None
    return value


def classify_wget_failure(returncode: int, log_text: str) -> str:
    text = log_text.lower()
    if any(marker in text for marker in ("401", "403", "unauthorized", "forbidden", "gated")):
        return "unauthorized"
    if returncode == 0:
        return "ok"
    return "error"


def wget_command(url: str, dest: Path, token: str | None) -> list[str]:
    command = [
        "wget",
        "--tries=5",
        "--timeout=60",
        "--retry-connrefused",
        "--continue",
        "--server-response",
        "--progress=dot:giga",
        "--user-agent=ai-image-studio/1.0",
        "-O",
        str(dest),
    ]
    if token:
        command.extend(["--header", f"Authorization: Bearer {token}"])
    command.append(url)
    return command


def fetch_token(region: str) -> str | None:
    try:
        result = subprocess.run(
            [
                "aws",
                "ssm",
                "get-parameter",
                "--region",
                region,
                "--name",
                "/ai-image-studio/hf-token",
                "--with-decryption",
                "--query",
                "Parameter.Value",
                "--output",
                "text",
            ],
            check=False,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.TimeoutExpired):
        return None
    if result.returncode != 0:
        return None
    return normalize_token(result.stdout)


class DownloadResult:
    def __init__(self, kind: str, detail: str = "") -> None:
        self.kind = kind
        self.detail = detail


def download_file(
    url: str,
    dest: Path,
    token: str | None,
    min_bytes: int,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> DownloadResult:
    dest.parent.mkdir(parents=True, exist_ok=True)
    partial = dest.with_name(dest.name + ".partial")
    command = wget_command(url, partial, token)
    try:
        completed = runner(
            command,
            check=False,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return DownloadResult("error", str(exc))
    log = f"{completed.stdout or ''}{completed.stderr or ''}"
    size = partial.stat().st_size if partial.exists() else 0
    if completed.returncode == 0 and size >= min_bytes:
        partial.replace(dest)
        return DownloadResult("ok")
    kind = "toosmall" if completed.returncode == 0 else classify_wget_failure(completed.returncode, log)
    if kind in {"unauthorized", "toosmall"} and partial.exists():
        partial.unlink()
    tail = log.strip().splitlines()[-8:]
    return DownloadResult(kind, "\n".join(tail)[:500])


def ensure_model(
    model: dict,
    dest_root: Path,
    token_getter: Callable[[], str | None],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> str:
    dest = dest_root / model["dest"]
    min_bytes = int(model["min_bytes"])
    if dest.is_file() and dest.stat().st_size >= min_bytes:
        print(f"present {model['id']}", flush=True)
        return "present"
    if dest.exists():
        dest.unlink()

    required = bool(model["required"])
    deadline = clock() + (AUTH_WAIT_SECONDS if required else 0)
    last = DownloadResult("error", "no url attempted")
    attempts = 0
    while attempts < 40:
        attempts += 1
        token = token_getter()
        unauthorized = False
        for url in model["urls"]:
            print(f"downloading {model['id']} from {url}", flush=True)
            last = download_file(url, dest, token, min_bytes, runner)
            if last.kind == "ok":
                print(f"saved {dest}", flush=True)
                return "downloaded"
            print(f"{model['id']} from {url}: {last.kind}", flush=True)
            if last.detail:
                print(last.detail, flush=True)
            if last.kind == "unauthorized":
                unauthorized = True
        if required and unauthorized and token is None and clock() < deadline:
            print("waiting for a Hugging Face token in SSM before retrying", flush=True)
            sleep(AUTH_POLL_SECONDS)
            continue
        break

    message = f"{model['id']} was not downloaded ({last.kind})"
    if required:
        raise SystemExit(
            message
            + ". Accept the model license, store HF_TOKEN, and start the instance again so setup can retry."
        )
    print(f"warning: optional model skipped: {message}", flush=True)
    return "skipped"


def iter_models(manifest: dict) -> Iterable[dict]:
    required = [model for model in manifest["models"] if model["required"]]
    optional = [model for model in manifest["models"] if not model["required"]]
    return [*required, *optional]


def download_manifest(
    manifest: dict,
    dest_root: Path,
    token_getter: Callable[[], str | None],
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.time,
) -> None:
    for model in iter_models(manifest):
        ensure_model(model, dest_root, token_getter, runner, sleep, clock)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Cache ComfyUI weights from the studio manifest.")
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--dest", type=Path, required=True)
    parser.add_argument("--region", required=True)
    args = parser.parse_args(argv)
    manifest = json.loads(args.manifest.read_text(encoding="utf-8"))
    download_manifest(manifest, args.dest, lambda: fetch_token(args.region))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
