"""Small ComfyUI HTTP helpers. The CLI talks to 127.0.0.1 through an SSM tunnel."""

from __future__ import annotations

import json
import secrets
import urllib.parse
from pathlib import Path

from workflows import IMAGE_SUFFIXES


def safe_upload_name(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        raise ValueError(f"unsupported image type {suffix or '(none)'}")
    return f"studio-{secrets.token_hex(8)}{suffix}"


def encode_multipart(path: Path, upload_name: str) -> tuple[bytes, str]:
    boundary = f"----StudioBoundary{secrets.token_hex(16)}"
    payload = path.read_bytes()
    body = bytearray()
    for name, value in (("overwrite", "true"), ("type", "input")):
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'.encode())
    body.extend(f"--{boundary}\r\n".encode())
    disposition = f'Content-Disposition: form-data; name="image"; filename="{upload_name}"\r\n'
    body.extend(disposition.encode())
    body.extend(b"Content-Type: application/octet-stream\r\n\r\n")
    body.extend(payload)
    body.extend(f"\r\n--{boundary}--\r\n".encode())
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def prompt_id_from_response(payload: dict) -> str:
    errors = payload.get("node_errors") or {}
    if errors:
        raise RuntimeError(f"ComfyUI rejected the workflow: {json.dumps(errors)[:800]}")
    if payload.get("error"):
        raise RuntimeError(f"ComfyUI rejected the workflow: {payload['error']}")
    prompt_id = payload.get("prompt_id")
    if not prompt_id:
        raise RuntimeError(f"unexpected ComfyUI response: {payload}")
    return str(prompt_id)


def image_from_history(history: dict, prompt_id: str, save_node: str) -> dict | None:
    entry = history.get(prompt_id)
    if not isinstance(entry, dict):
        return None
    status = entry.get("status") or {}
    if status.get("status_str") == "error":
        raise RuntimeError(f"ComfyUI job failed: {status.get('messages')}")
    images = ((entry.get("outputs") or {}).get(save_node) or {}).get("images") or []
    if images:
        return images[0]
    if status.get("completed"):
        raise RuntimeError("ComfyUI finished without an image")
    return None


def view_query(image: dict) -> str:
    return urllib.parse.urlencode(
        {
            "filename": image["filename"],
            "subfolder": image.get("subfolder") or "",
            "type": image.get("type") or "output",
        }
    )
