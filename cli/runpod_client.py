"""RunPod serverless HTTP calls. The API key stays in the caller's environment."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

ENDPOINT_ID = re.compile(r"^[A-Za-z0-9_-]{1,80}$")
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED", "TIMED_OUT"}


def require_endpoint_id(value: str) -> str:
    if not ENDPOINT_ID.fullmatch(value or ""):
        raise ValueError("Set RUNPOD_ENDPOINT_ID to the endpoint id from the RunPod console")
    return value


def run_url(endpoint_id: str) -> str:
    return f"https://api.runpod.ai/v2/{require_endpoint_id(endpoint_id)}/run"


def status_url(endpoint_id: str, job_id: str) -> str:
    if not job_id or "/" in job_id:
        raise ValueError("RunPod job id is missing")
    return f"https://api.runpod.ai/v2/{require_endpoint_id(endpoint_id)}/status/{job_id}"


def interpret_status(body: dict) -> tuple[str, dict | None, str | None]:
    status = str(body.get("status") or "UNKNOWN")
    if status == "COMPLETED":
        output = body.get("output")
        if isinstance(output, dict) and output.get("error"):
            return "FAILED", None, str(output["error"])
        return status, output if isinstance(output, dict) else None, None
    if status in TERMINAL:
        return status, None, str(body.get("error") or status)
    return status, None, None


def output_key(output: dict | None) -> str:
    if not isinstance(output, dict) or not output.get("output_key"):
        raise ValueError("RunPod did not return an output_key")
    return str(output["output_key"])


def post_json(url: str, payload: dict, api_key: str, timeout: int = 60) -> dict:
    data = json.dumps(payload).encode()
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Content-Type", "application/json")
    request.add_header("Authorization", f"Bearer {api_key}")
    return _read(request, timeout)


def get_json(url: str, api_key: str, timeout: int = 60) -> dict:
    request = urllib.request.Request(url, method="GET")
    request.add_header("Authorization", f"Bearer {api_key}")
    return _read(request, timeout)


def _read(request: urllib.request.Request, timeout: int) -> dict:
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode())
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode(errors="replace")[:500]
        raise RuntimeError(f"RunPod returned {exc.code}: {detail}") from exc
