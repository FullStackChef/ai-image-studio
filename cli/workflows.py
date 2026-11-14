"""ComfyUI API graphs for the models installed by the instance setup."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "config" / "models.manifest.json"
SAVE_NODE = "90"
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp"}


@dataclass
class GenerationRequest:
    mode: str
    prompt: str
    negative: str
    width: int
    height: int
    steps: int
    guidance: float
    denoise: float
    seed: int
    filename_prefix: str
    sampler: str
    scheduler: str
    init_name: str | None = None
    mask_name: str | None = None
    reference_name: str | None = None
    ip_weight: float = 0.8


def load_manifest(path: Path | None = None) -> dict:
    return json.loads((path or DEFAULT_MANIFEST).read_text(encoding="utf-8"))


def model_filename(manifest: dict, model_id: str) -> str:
    for model in manifest["models"]:
        if model["id"] == model_id:
            return model["filename"]
    raise KeyError(model_id)


def resolve_mode(mode: str, init: Path | None, mask: Path | None, reference: Path | None) -> str:
    if mode == "auto":
        if mask and reference:
            raise ValueError("use --mask with --input for inpaint, or --reference on its own")
        if mask:
            chosen = "inpaint"
        elif reference and init:
            raise ValueError("pass either --reference or --input, or set --mode")
        elif reference:
            chosen = "reference"
        elif init:
            chosen = "img2img"
        else:
            chosen = "txt2img"
    else:
        chosen = mode

    if chosen == "inpaint" and (init is None or mask is None):
        raise ValueError("inpaint requires --input and --mask")
    if chosen == "img2img" and init is None:
        raise ValueError("img2img requires --input")
    if chosen == "reference" and reference is None:
        raise ValueError("reference requires --reference")
    if chosen == "txt2img" and (init or mask or reference):
        raise ValueError("txt2img does not use --input, --mask, or --reference")
    if mask and chosen != "inpaint":
        raise ValueError("--mask is only used for inpaint")
    return chosen


def validate_image(path: Path | None, label: str) -> None:
    if path is None:
        return
    if not path.is_file():
        raise ValueError(f"{label} not found: {path}")
    if path.suffix.lower() not in IMAGE_SUFFIXES:
        raise ValueError(f"{label} must be a png, jpg, webp, or bmp file")


def validate_dimensions(width: int, height: int) -> None:
    if width % 16 or height % 16:
        raise ValueError("width and height must be multiples of 16")
    if not 256 <= width <= 2048 or not 256 <= height <= 2048:
        raise ValueError("width and height must be between 256 and 2048")


def apply_defaults(mode: str, steps: int | None, guidance: float | None, denoise: float | None) -> tuple[int, float, float]:
    if mode == "reference":
        steps = 30 if steps is None else steps
        guidance = 7.0 if guidance is None else guidance
        denoise = 1.0 if denoise is None else denoise
    else:
        steps = 4 if steps is None else steps
        guidance = 3.5 if guidance is None else guidance
        if denoise is None:
            denoise = {"txt2img": 1.0, "img2img": 0.55, "inpaint": 0.85}[mode]
    if not 1 <= steps <= 100:
        raise ValueError("steps must be between 1 and 100")
    if not 0 <= guidance <= 30:
        raise ValueError("guidance must be between 0 and 30")
    if not 0 < denoise <= 1:
        raise ValueError("denoise must be greater than 0 and at most 1")
    return steps, guidance, denoise


def _check_links(workflow: dict) -> None:
    for node_id, node in workflow.items():
        if "class_type" not in node or "inputs" not in node:
            raise ValueError(f"node {node_id} is missing class_type or inputs")
        for value in node["inputs"].values():
            if isinstance(value, list) and len(value) == 2 and isinstance(value[0], str):
                if value[0] not in workflow:
                    raise ValueError(f"node {node_id} links to missing node {value[0]}")


def _flux_base(request: GenerationRequest, manifest: dict, latent_node: str) -> dict:
    return {
        "1": {
            "class_type": "DualCLIPLoader",
            "inputs": {
                "clip_name1": model_filename(manifest, "flux_t5"),
                "clip_name2": model_filename(manifest, "flux_clip_l"),
                "type": "flux",
            },
        },
        "2": {
            "class_type": "UNETLoader",
            "inputs": {
                "unet_name": model_filename(manifest, "flux_unet"),
                "weight_dtype": "default",
            },
        },
        "3": {
            "class_type": "VAELoader",
            "inputs": {"vae_name": model_filename(manifest, "flux_vae")},
        },
        "4": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": request.prompt, "clip": ["1", 0]},
        },
        "5": {
            "class_type": "FluxGuidance",
            "inputs": {"guidance": request.guidance, "conditioning": ["4", 0]},
        },
        "7": {"class_type": "RandomNoise", "inputs": {"noise_seed": request.seed}},
        "8": {"class_type": "KSamplerSelect", "inputs": {"sampler_name": request.sampler}},
        "9": {
            "class_type": "BasicScheduler",
            "inputs": {
                "scheduler": request.scheduler,
                "steps": request.steps,
                "denoise": request.denoise,
                "model": ["2", 0],
            },
        },
        "10": {
            "class_type": "BasicGuider",
            "inputs": {"model": ["2", 0], "conditioning": ["5", 0]},
        },
        "11": {
            "class_type": "SamplerCustomAdvanced",
            "inputs": {
                "noise": ["7", 0],
                "guider": ["10", 0],
                "sampler": ["8", 0],
                "sigmas": ["9", 0],
                "latent_image": [latent_node, 0],
            },
        },
        "12": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["11", 0], "vae": ["3", 0]},
        },
        SAVE_NODE: {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": request.filename_prefix, "images": ["12", 0]},
        },
    }


def build_workflow(request: GenerationRequest, manifest: dict) -> tuple[dict, str]:
    if request.mode == "reference":
        workflow = _reference_workflow(request, manifest)
    elif request.mode == "txt2img":
        workflow = _flux_base(request, manifest, "6")
        workflow["6"] = {
            "class_type": "EmptySD3LatentImage",
            "inputs": {"width": request.width, "height": request.height, "batch_size": 1},
        }
    elif request.mode == "img2img":
        workflow = _flux_base(request, manifest, "13")
        workflow["6"] = {"class_type": "LoadImage", "inputs": {"image": request.init_name}}
        workflow["13"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["6", 0], "vae": ["3", 0]},
        }
    elif request.mode == "inpaint":
        workflow = _flux_base(request, manifest, "16")
        workflow["6"] = {"class_type": "LoadImage", "inputs": {"image": request.init_name}}
        workflow["14"] = {"class_type": "LoadImage", "inputs": {"image": request.mask_name}}
        workflow["15"] = {
            "class_type": "ImageToMask",
            "inputs": {"image": ["14", 0], "channel": "red"},
        }
        workflow["13"] = {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["6", 0], "vae": ["3", 0]},
        }
        workflow["16"] = {
            "class_type": "SetLatentNoiseMask",
            "inputs": {"samples": ["13", 0], "mask": ["15", 0]},
        }
    else:
        raise ValueError(f"unknown mode {request.mode}")
    _check_links(workflow)
    return workflow, SAVE_NODE


def _reference_workflow(request: GenerationRequest, manifest: dict) -> dict:
    negative = request.negative.strip() or "blurry, low quality, watermark, text"
    return {
        "1": {
            "class_type": "CheckpointLoaderSimple",
            "inputs": {"ckpt_name": model_filename(manifest, "sdxl_base")},
        },
        "2": {
            "class_type": "CLIPVisionLoader",
            "inputs": {"clip_name": model_filename(manifest, "clip_vision_vit_h")},
        },
        "3": {
            "class_type": "IPAdapterModelLoader",
            "inputs": {"ipadapter_file": model_filename(manifest, "ip_adapter_sdxl")},
        },
        "4": {"class_type": "LoadImage", "inputs": {"image": request.reference_name}},
        "5": {
            "class_type": "IPAdapterAdvanced",
            "inputs": {
                "model": ["1", 0],
                "ipadapter": ["3", 0],
                "image": ["4", 0],
                "weight": request.ip_weight,
                "weight_type": "linear",
                "combine_embeds": "concat",
                "start_at": 0.0,
                "end_at": 1.0,
                "embeds_scaling": "V only",
                "clip_vision": ["2", 0],
            },
        },
        "6": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": request.prompt, "clip": ["1", 1]},
        },
        "7": {
            "class_type": "CLIPTextEncode",
            "inputs": {"text": negative, "clip": ["1", 1]},
        },
        "8": {
            "class_type": "EmptyLatentImage",
            "inputs": {"width": request.width, "height": request.height, "batch_size": 1},
        },
        "9": {
            "class_type": "KSampler",
            "inputs": {
                "seed": request.seed,
                "steps": request.steps,
                "cfg": request.guidance,
                "sampler_name": request.sampler,
                "scheduler": request.scheduler,
                "denoise": request.denoise,
                "model": ["5", 0],
                "positive": ["6", 0],
                "negative": ["7", 0],
                "latent_image": ["8", 0],
            },
        },
        "10": {
            "class_type": "VAEDecode",
            "inputs": {"samples": ["9", 0], "vae": ["1", 2]},
        },
        SAVE_NODE: {
            "class_type": "SaveImage",
            "inputs": {"filename_prefix": request.filename_prefix, "images": ["10", 0]},
        },
    }
