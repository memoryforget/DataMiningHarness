from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any



def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Standalone MinerU wrapper for PDF-to-markdown extraction.")
    parser.add_argument("--pdf", required=True, help="Path to the source PDF.")
    parser.add_argument("--output-dir", default="./mineru_runs", help="Directory for parse outputs and caches.")
    parser.add_argument(
        "--backend-mode",
        choices=["local"],
        default="local",
        help="Use the local MinerU wrapper path.",
    )
    parser.add_argument("--force-reparse", action="store_true", help="Re-run parsing even if cached output exists.")
    parser.add_argument("--timeout-seconds", type=int, default=600, help="Maximum wait time for parsing.")
    parser.add_argument("--language", default=os.environ.get("MINERU_LANGUAGE", "ch"), help="MinerU language option.")
    parser.add_argument("--local-backend", default=os.environ.get("MINERU_BACKEND", "vlm-auto-engine"))
    parser.add_argument("--local-method", default=os.environ.get("MINERU_METHOD", "auto"))
    parser.add_argument("--model-dir", default=os.environ.get("MINERU_MODEL_DIR", ""))
    parser.add_argument("--model-type", default=os.environ.get("MINERU_MODEL_TYPE", "vlm"))
    parser.add_argument("--model-source", default=os.environ.get("MINERU_MODEL_SOURCE", "local"))
    parser.add_argument("--vllm-use-v1", default=os.environ.get("VLLM_USE_V1", "1"))
    parser.add_argument("--cuda-visible-devices", default=os.environ.get("CUDA_VISIBLE_DEVICES", "0"))
    parser.add_argument("--local-api-url", default=os.environ.get("MINERU_LOCAL_API_URL", ""))
    return parser


def main() -> int:
    args = build_parser().parse_args()
    pdf_path = os.path.abspath(args.pdf)
    output_dir = os.path.abspath(args.output_dir)
    os.makedirs(output_dir, exist_ok=True)

    if not os.path.exists(pdf_path):
        print(json.dumps({"error": f"PDF not found: {pdf_path}"}, ensure_ascii=False, indent=2))
        return 1

    document_id = build_document_id(pdf_path, args.backend_mode)
    document_dir = os.path.join(output_dir, document_id)
    manifest_path = os.path.join(document_dir, "manifest.json")

    if os.path.exists(manifest_path) and not args.force_reparse:
        with open(manifest_path, "r", encoding="utf-8") as handle:
            payload = json.load(handle)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if not payload.get("error") else 1

    os.makedirs(document_dir, exist_ok=True)
    shutil.copy2(pdf_path, os.path.join(document_dir, os.path.basename(pdf_path)))

    payload = run_local_backend(args, pdf_path, document_dir)

    with open(manifest_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if not payload.get("error") else 1


def run_local_backend(args: argparse.Namespace, pdf_path: str, document_dir: str) -> dict[str, Any]:
    if not args.local_api_url:
        model_dir = str(args.model_dir or "").strip()
        if not model_dir:
            return error_payload("mineru_local", "MINERU_MODEL_DIR is required for local backend.")
        if not os.path.isdir(model_dir):
            return error_payload("mineru_local", f"Local MinerU model dir not found: {model_dir}")

    output_dir = os.path.join(document_dir, "mineru_local_output")
    os.makedirs(output_dir, exist_ok=True)

    command = [
        "mineru",
        "-p",
        pdf_path,
        "-o",
        output_dir,
        "-b",
        args.local_backend,
        "-m",
        args.local_method,
        "-l",
        args.language,
    ]
    if args.local_api_url:
        command.extend(["--api-url", args.local_api_url])

    env = os.environ.copy()
    config_path = None
    if not args.local_api_url:
        home_dir = os.path.join(document_dir, "mineru_local_home")
        os.makedirs(home_dir, exist_ok=True)
        config_name = "mineru_tools.json"
        config_path = os.path.join(home_dir, config_name)
        with open(config_path, "w", encoding="utf-8") as handle:
            json.dump({"models-dir": {args.model_type: str(args.model_dir)}}, handle, ensure_ascii=False, indent=2)
        env["HOME"] = home_dir
        env["MINERU_TOOLS_CONFIG_JSON"] = config_name
        env["MINERU_MODEL_SOURCE"] = args.model_source
        env["VLLM_USE_V1"] = normalize_env_bool(args.vllm_use_v1)
        if args.cuda_visible_devices:
            env["CUDA_VISIBLE_DEVICES"] = args.cuda_visible_devices

    proc = subprocess.run(
        command,
        text=True,
        capture_output=True,
        env=env,
        timeout=max(args.timeout_seconds, 300),
        cwd=document_dir,
        check=False,
    )
    if proc.returncode != 0:
        return error_payload(
            "mineru_local",
            "Local MinerU CLI failed",
            extra={
                "command": command,
                "stdout": proc.stdout,
                "stderr": proc.stderr,
            },
        )

    pdf_name = Path(pdf_path).stem
    parse_dir_name = resolve_local_parse_dir_name(args.local_backend, args.local_method)
    extract_dir = os.path.join(output_dir, pdf_name, parse_dir_name)
    if not os.path.isdir(extract_dir):
        return error_payload("mineru_local", f"Local MinerU output dir not found: {extract_dir}")

    markdown_path = locate_markdown_artifact(extract_dir)
    warnings = []
    if not markdown_path:
        warnings.append("No markdown artifact found in MinerU extraction output.")

    return {
        "pdf_path": pdf_path,
        "backend": "mineru_local",
        "status": "ready" if markdown_path else "partial",
        "markdown_path": markdown_path,
        "extract_dir": extract_dir,
        "warnings": warnings,
        "error": None if markdown_path else "MinerU returned no usable markdown artifact.",
        "stdout": proc.stdout,
        "stderr": proc.stderr,
        "config_path": config_path,
    }


def locate_markdown_artifact(root_dir: str) -> str | None:
    matches: list[str] = []
    for current_root, _, files in os.walk(root_dir):
        for file_name in sorted(files):
            if file_name.endswith(".md"):
                matches.append(os.path.join(current_root, file_name))
    if not matches:
        return None
    if len(matches) > 1:
        raise RuntimeError(f"Multiple markdown artifacts found: {[os.path.relpath(p, root_dir) for p in matches]}")
    return matches[0]


def build_document_id(pdf_path: str, backend_mode: str) -> str:
    file_hash = sha256_file(pdf_path)
    digest = hashlib.sha256(f"{file_hash}:{backend_mode}".encode("utf-8")).hexdigest()
    return f"doc_{digest[:16]}"


def sha256_file(path: str) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def safe_stem(file_name: str) -> str:
    stem, _ = os.path.splitext(file_name)
    return "".join(char if char.isalnum() or char in {"-", "_", "."} else "_" for char in stem)


def resolve_local_parse_dir_name(backend: str, method: str) -> str:
    if backend.startswith("pipeline"):
        return method
    if backend.startswith("vlm"):
        return "vlm"
    if backend.startswith("hybrid"):
        return f"hybrid_{method}"
    raise RuntimeError(f"Unsupported MinerU local backend: {backend}")


def parse_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return False
    return str(value).strip().lower() in {"1", "true", "yes", "y"}


def normalize_env_bool(value: Any) -> str:
    return "1" if parse_bool(value) else "0"


def error_payload(backend: str, message: str, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {
        "backend": backend,
        "status": "error",
        "markdown_path": None,
        "extract_dir": None,
        "warnings": [message],
        "error": message,
    }
    if extra:
        payload.update(extra)
    return payload


if __name__ == "__main__":
    raise SystemExit(main())
