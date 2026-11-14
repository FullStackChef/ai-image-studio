"""Unit tests that do not need AWS, a GPU, or ComfyUI."""

from __future__ import annotations

import contextlib
import io
import json
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLI = ROOT / "cli"
sys.path.insert(0, str(CLI))
sys.path.insert(0, str(ROOT / "instance"))

import comfy_client  # noqa: E402
import download_models  # noqa: E402
import workflows  # noqa: E402

sys.path.insert(0, str(ROOT / "worker"))
sys.path.insert(0, str(CLI))
import handler  # noqa: E402
import runpod_client  # noqa: E402


class ManifestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = workflows.load_manifest()

    def test_required_flux_files_are_named_in_the_manifest(self) -> None:
        ids = {model["id"] for model in self.manifest["models"] if model["required"]}
        self.assertEqual(ids, {"flux_unet", "flux_clip_l", "flux_t5", "flux_vae"})

    def test_destinations_match_filenames_and_use_https(self) -> None:
        for model in self.manifest["models"]:
            self.assertEqual(Path(model["dest"]).name, model["filename"])
            self.assertGreater(model["min_bytes"], 1_000_000)
            self.assertTrue(model["urls"])
            for url in model["urls"]:
                self.assertTrue(url.startswith("https://"), url)


class WorkflowTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manifest = workflows.load_manifest()

    def _request(self, mode: str, **kwargs: object) -> workflows.GenerationRequest:
        steps, guidance, denoise = workflows.apply_defaults(mode, None, None, None)
        data = dict(
            mode=mode,
            prompt="a red ceramic teapot on a wooden table",
            negative="",
            width=1024,
            height=1024,
            steps=steps,
            guidance=guidance,
            denoise=denoise,
            seed=7,
            filename_prefix="studio",
            sampler="euler",
            scheduler="simple" if mode != "reference" else "normal",
            init_name="photo.png",
            mask_name="mask.png",
            reference_name="reference.png",
            ip_weight=0.8,
        )
        data.update(kwargs)
        return workflows.GenerationRequest(**data)  # type: ignore[arg-type]

    def test_txt2img_uses_flux_and_saves(self) -> None:
        workflow, save_node = workflows.build_workflow(self._request("txt2img"), self.manifest)
        self.assertEqual(save_node, "90")
        self.assertEqual(workflow["2"]["inputs"]["unet_name"], "flux1-schnell-fp8.safetensors")
        self.assertEqual(workflow["6"]["class_type"], "EmptySD3LatentImage")
        self.assertEqual(workflow["90"]["class_type"], "SaveImage")
        self.assertNotIn("IPAdapterAdvanced", {node["class_type"] for node in workflow.values()})

    def test_img2img_encodes_the_init_image(self) -> None:
        workflow, _ = workflows.build_workflow(self._request("img2img"), self.manifest)
        self.assertEqual(workflow["13"]["class_type"], "VAEEncode")
        self.assertEqual(workflow["6"]["inputs"]["image"], "photo.png")
        classes = {node["class_type"] for node in workflow.values()}
        self.assertNotIn("EmptySD3LatentImage", classes)

    def test_inpaint_masks_the_latent(self) -> None:
        workflow, _ = workflows.build_workflow(self._request("inpaint"), self.manifest)
        self.assertEqual(workflow["15"]["inputs"]["channel"], "red")
        self.assertEqual(workflow["16"]["class_type"], "SetLatentNoiseMask")
        self.assertEqual(workflow["11"]["inputs"]["latent_image"], ["16", 0])

    def test_reference_uses_ip_adapter_and_sdxl(self) -> None:
        workflow, _ = workflows.build_workflow(self._request("reference"), self.manifest)
        self.assertEqual(workflow["5"]["class_type"], "IPAdapterAdvanced")
        self.assertEqual(workflow["1"]["inputs"]["ckpt_name"], "sd_xl_base_1.0.safetensors")
        self.assertEqual(workflow["7"]["inputs"]["text"], "blurry, low quality, watermark, text")

    def test_dimensions_must_be_multiples_of_16(self) -> None:
        with self.assertRaises(ValueError):
            workflows.validate_dimensions(1000, 1024)

    def test_mode_selection(self) -> None:
        self.assertEqual(workflows.resolve_mode("auto", None, None, None), "txt2img")
        self.assertEqual(workflows.resolve_mode("auto", Path("a.png"), None, None), "img2img")
        self.assertEqual(workflows.resolve_mode("auto", Path("a.png"), Path("m.png"), None), "inpaint")
        self.assertEqual(workflows.resolve_mode("auto", None, None, Path("r.png")), "reference")
        with self.assertRaises(ValueError):
            workflows.resolve_mode("txt2img", Path("a.png"), None, None)


class ComfyClientTests(unittest.TestCase):
    def test_multipart_contains_the_file_bytes(self) -> None:
        path = ROOT / "cli" / "tests" / "tiny.png"
        path.write_bytes(b"\x89PNG test-bytes")
        try:
            body, content_type = comfy_client.encode_multipart(path, "studio-test.png")
        finally:
            path.unlink()
        self.assertIn("multipart/form-data; boundary=", content_type)
        self.assertIn(b"studio-test.png", body)
        self.assertIn(b"\x89PNG test-bytes", body)

    def test_prompt_and_history_parsing(self) -> None:
        self.assertEqual(comfy_client.prompt_id_from_response({"prompt_id": "abc", "node_errors": {}}), "abc")
        with self.assertRaises(RuntimeError):
            comfy_client.prompt_id_from_response({"node_errors": {"1": "missing"}})
        image = comfy_client.image_from_history(
            {"abc": {"outputs": {"90": {"images": [{"filename": "studio_1.png", "type": "output"}]}}}},
            "abc",
            "90",
        )
        self.assertEqual(image["filename"], "studio_1.png")
        self.assertIsNone(comfy_client.image_from_history({}, "abc", "90"))
        with self.assertRaises(RuntimeError):
            comfy_client.image_from_history({"abc": {"status": {"status_str": "error", "messages": ["boom"]}}}, "abc", "90")


class DownloadTests(unittest.TestCase):
    def test_token_normalization_and_wget_header(self) -> None:
        self.assertIsNone(download_models.normalize_token(" unset "))
        self.assertEqual(download_models.normalize_token("hf_example"), "hf_example")
        plain = download_models.wget_command("https://example.test/file", Path("file.partial"), None)
        self.assertNotIn("--header", plain)
        authed = download_models.wget_command("https://example.test/file", Path("file.partial"), "hf_example")
        self.assertIn("Authorization: Bearer hf_example", authed)

    def test_optional_model_is_skipped_when_unauthorized(self) -> None:
        model = {
            "id": "sdxl_base",
            "dest": "models/checkpoints/sd_xl_base_1.0.safetensors",
            "urls": ["https://example.test/sdxl"],
            "required": False,
            "min_bytes": 10,
        }

        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 8, "", "HTTP/1.1 401 Unauthorized")

        status = download_models.ensure_model(model, Path("/tmp"), lambda: None, runner, lambda _seconds: None, lambda: 0)
        self.assertEqual(status, "skipped")

    def test_required_model_fails_clearly_without_a_token(self) -> None:
        model = {
            "id": "flux_vae",
            "dest": "models/vae/ae.safetensors",
            "urls": ["https://example.test/ae"],
            "required": True,
            "min_bytes": 10,
        }

        def runner(command, **_kwargs):
            return subprocess.CompletedProcess(command, 8, "", "HTTP/1.1 403 Forbidden")

        state = {"now": 0.0}

        def clock() -> float:
            return state["now"]

        def sleep(seconds: float) -> None:
            state["now"] += seconds

        with self.assertRaises(SystemExit), contextlib.redirect_stdout(io.StringIO()):
            download_models.ensure_model(model, Path("/tmp"), lambda: None, runner, sleep, clock)


class RunPodTests(unittest.TestCase):
    def test_worker_image_does_not_bake_weights(self) -> None:
        text = (ROOT / "worker" / "Dockerfile").read_text(encoding="utf-8")
        self.assertNotIn("huggingface.co", text.lower())
        self.assertIn("v0.45.0", text)
        self.assertIn("handler.py", text)

    def test_network_volume_is_linked_over_the_image_tree(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            root = Path(raw)
            volume = root / "volume"
            comfy = root / "ComfyUI"
            (volume / "models" / "unet").mkdir(parents=True)
            (volume / "models" / "unet" / "flux.safetensors").write_bytes(b"weights")
            (comfy / "models" / "unet").mkdir(parents=True)
            handler.link_models(volume, comfy)
            self.assertTrue((comfy / "models").is_symlink())
            self.assertEqual((comfy / "models" / "unet" / "flux.safetensors").read_bytes(), b"weights")

    def test_missing_volume_is_rejected(self) -> None:
        import tempfile

        with tempfile.TemporaryDirectory() as raw:
            with self.assertRaises(FileNotFoundError):
                handler.link_models(Path(raw), Path(raw) / "ComfyUI")

    def test_status_parser(self) -> None:
        status, output, error = runpod_client.interpret_status(
            {"status": "COMPLETED", "output": {"output_key": "outputs/studio-1.png"}}
        )
        self.assertEqual(status, "COMPLETED")
        self.assertEqual(runpod_client.output_key(output), "outputs/studio-1.png")
        self.assertIsNone(error)
        status, output, error = runpod_client.interpret_status(
            {"status": "COMPLETED", "output": {"error": "weights missing"}}
        )
        self.assertEqual(status, "FAILED")
        self.assertIn("weights", error or "")
        self.assertEqual(runpod_client.interpret_status({"status": "IN_QUEUE"})[0], "IN_QUEUE")
        with self.assertRaises(ValueError):
            runpod_client.require_endpoint_id("not an id")


class DryRunTests(unittest.TestCase):
    def test_run_dry_run_prints_a_workflow(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI / "studio.py"), "run", "--prompt", "a red ceramic teapot", "--dry-run", "--seed", "3"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        payload = json.loads(result.stdout)
        self.assertEqual(payload["mode"], "txt2img")
        self.assertEqual(payload["seed"], 3)
        self.assertEqual(payload["workflow"]["90"]["class_type"], "SaveImage")

    def test_bad_dimension_is_a_usage_error(self) -> None:
        result = subprocess.run(
            [sys.executable, str(CLI / "studio.py"), "run", "--prompt", "teapot", "--width", "1000", "--dry-run"],
            check=False,
            capture_output=True,
            text=True,
        )
        self.assertEqual(result.returncode, 2)
        self.assertIn("multiples of 16", result.stderr)


if __name__ == "__main__":
    unittest.main()
