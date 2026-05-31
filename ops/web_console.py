"""Training Operations Web Console Server for Dana's continuous training system.

Provides a secure, offline-only browser console interface and API endpoints.
"""

from __future__ import annotations

import os
import sys
import json
import shutil
import asyncio
import re
import urllib.parse
from datetime import datetime, timezone
from pathlib import Path
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from typing import Any, Optional, Dict, Tuple

from storage.repository import Repository
from ops.training_console import TrainingOperationsConsole, TrainingConsoleConfig


# Global Constants
MAX_UPLOAD_SIZE = 10 * 1024 * 1024  # 10 MB

ALLOWED_SOURCE_TYPES = {
    "call_transcript": "data/imports/call_transcripts",
    "youtube": "data/imports/youtube_training",
    "manager_note": "data/imports/manager_notes",
    "licensed_agent_feedback": "data/imports/licensed_agent_feedback",
    "post_call": "data/imports/post_call_payloads",
    "strategy_doc": "data/imports/strategy_docs",
}

ALLOWED_EXTENSIONS = {".txt", ".json", ".jsonl", ".md"}


class TrainingWebConsoleConfig:
    """Configuration for the Training Operations Web Console."""

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8787,
        static_dir: str = "static/training_console",
        output_dir: str = "data/training_console",
        data_dir: Optional[str] = None,
        allow_remote: bool = False,
        debug: bool = False,
    ) -> None:
        self.host = host
        self.port = port
        self.static_dir = static_dir
        self.output_dir = output_dir
        self.data_dir = data_dir
        self.allow_remote = allow_remote
        self.debug = debug


def parse_multipart(body_bytes: bytes, boundary: bytes) -> Tuple[Dict[str, str], Dict[str, Tuple[str, bytes]]]:
    """Manually parse multipart/form-data request body to prevent cgi module deprecation issues in Python 3.13.

    Returns:
        fields: Dictionary of field name -> string value
        files: Dictionary of field name -> tuple of (filename, file_bytes)
    """
    fields: Dict[str, str] = {}
    files: Dict[str, Tuple[str, bytes]] = {}

    part_boundary = b"--" + boundary
    parts = body_bytes.split(part_boundary)

    for part in parts:
        part = part.strip()
        if not part or part == b"--":
            continue

        if b"\r\n\r\n" in part:
            headers_part, data_part = part.split(b"\r\n\r\n", 1)
        elif b"\n\n" in part:
            headers_part, data_part = part.split(b"\n\n", 1)
        else:
            continue

        if data_part.endswith(b"\r\n"):
            data_part = data_part[:-2]
        elif data_part.endswith(b"\n"):
            data_part = data_part[:-1]

        headers_str = headers_part.decode("utf-8", errors="ignore")
        name = None
        filename = None

        for line in headers_str.splitlines():
            if line.lower().startswith("content-disposition:"):
                name_match = re.search(r'name="([^"]+)"', line)
                filename_match = re.search(r'filename="([^"]+)"', line)
                if name_match:
                    name = name_match.group(1)
                if filename_match:
                    filename = filename_match.group(1)

        if name:
            if filename is not None:
                files[name] = (filename, data_part)
            else:
                fields[name] = data_part.decode("utf-8", errors="ignore")

    return fields, files


class WebConsoleHandler(BaseHTTPRequestHandler):
    """HTTP Request Handler serving static web console pages and continuous training APIs."""

    def log_message(self, format: str, *args: Any) -> None:
        # Direct logs to stderr instead of stdout so stdout remains reserved for clean JSON
        sys.stderr.write(f"[{self.log_date_time_string()}] {format % args}\n")

    def serve_static(self, rel_path: str) -> None:
        """Serve static files safely preventing directory traversal."""
        static_dir = Path(self.server.config.static_dir).resolve()
        if rel_path == "" or rel_path == "/":
            target = static_dir / "index.html"
        else:
            clean_path = rel_path.lstrip("/")
            target = (static_dir / clean_path).resolve()

        try:
            target.relative_to(static_dir)
        except ValueError:
            self.server.error_response(self, "Access Denied: Path outside static directory.", 403)
            return

        if not target.exists() or not target.is_file():
            self.server.error_response(self, "File Not Found", 404)
            return

        content_type = "text/plain"
        if target.suffix == ".html":
            content_type = "text/html"
        elif target.suffix == ".js":
            content_type = "application/javascript"
        elif target.suffix == ".css":
            content_type = "text/css"
        elif target.suffix == ".json":
            content_type = "application/json"

        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(target.stat().st_size))
        self.end_headers()
        with open(target, "rb") as f:
            shutil.copyfileobj(f, self.wfile)

    def do_GET(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if parsed_url.path.startswith("/api/"):
            try:
                status_code, res_payload = asyncio.run(
                    self.server.handle_api("GET", self.path, None, None)
                )
                self.server.json_response(self, res_payload, status_code)
            except Exception as e:
                self.server.error_response(self, str(e), 500)
        else:
            self.serve_static(parsed_url.path)

    def do_POST(self) -> None:
        parsed_url = urllib.parse.urlparse(self.path)
        if not parsed_url.path.startswith("/api/"):
            self.server.error_response(self, "Method Not Allowed for non-API routes.", 405)
            return

        try:
            content_type = self.headers.get("Content-Type", "")
            body = None
            files = None

            if "multipart/form-data" in content_type:
                upload_data = self.server.parse_multipart_upload(self)
                body = upload_data.get("fields")
                files = upload_data.get("files")
            elif "application/json" in content_type:
                body = self.server.parse_json_body(self)
            else:
                body = {}

            status_code, res_payload = asyncio.run(
                self.server.handle_api("POST", self.path, body, files)
            )
            self.server.json_response(self, res_payload, status_code)
        except Exception as e:
            self.server.error_response(self, str(e), 400)

    def do_OPTIONS(self) -> None:
        self.send_response(200)
        if self.server.config.debug:
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Headers", "*")
            self.send_header("Access-Control-Allow-Methods", "*")
        self.end_headers()


class TrainingWebConsoleServer(ThreadingHTTPServer):
    """Multithreaded local HTTP Server for Dana's training operations console."""

    def __init__(self, config: TrainingWebConsoleConfig, repository: Optional[Repository] = None) -> None:
        self.config = config
        self.repository = repository or Repository(data_dir=config.data_dir or "data")
        self.console = TrainingOperationsConsole(repository=self.repository)

        host = config.host
        if not config.allow_remote and host not in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"

        super().__init__((host, config.port), self.build_handler())

    def build_handler(self) -> type:
        return WebConsoleHandler

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        try:
            super().serve_forever(poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.server_close()

    def json_response(self, handler: WebConsoleHandler, payload: dict, status: int = 200) -> None:
        handler.send_response(status)
        handler.send_header("Content-Type", "application/json")
        if self.config.debug:
            handler.send_header("Access-Control-Allow-Origin", "*")
        body = json.dumps(payload, indent=2).encode("utf-8")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    def error_response(self, handler: WebConsoleHandler, message: str, status: int = 400, details: Optional[dict] = None) -> None:
        err_payload = {
            "success": False,
            "message": message,
            "error": message,
            "details": details or {},
        }
        self.json_response(handler, err_payload, status)

    def parse_json_body(self, handler: WebConsoleHandler) -> dict:
        content_length = int(handler.headers.get("Content-Length", 0))
        if content_length == 0:
            return {}
        body_bytes = handler.rfile.read(content_length)
        return json.loads(body_bytes.decode("utf-8"))

    def parse_multipart_upload(self, handler: WebConsoleHandler) -> dict:
        content_type = handler.headers.get("Content-Type", "")
        if "boundary=" not in content_type:
            raise ValueError("Content-Type is not multipart/form-data or lacks boundary.")
        boundary = content_type.split("boundary=")[1].strip().encode("utf-8")
        content_length = int(handler.headers.get("Content-Length", 0))

        if content_length > MAX_UPLOAD_SIZE:
            raise ValueError(f"Upload size exceeds maximum allowed limit of {MAX_UPLOAD_SIZE / (1024*1024)} MB.")

        body_bytes = handler.rfile.read(content_length)
        fields, files = parse_multipart(body_bytes, boundary)
        return {"fields": fields, "files": files}

    def safe_import_path(self, source_type: str, filename: str) -> Path:
        if source_type not in ALLOWED_SOURCE_TYPES:
            raise ValueError(f"Invalid source_type: {source_type}. Must be one of {list(ALLOWED_SOURCE_TYPES.keys())}")

        if ".." in filename or "/" in filename or "\\" in filename:
            raise ValueError("Invalid filename: contains unsupported characters or path traversal.")

        clean_name = Path(filename).name
        if not re.match(r"^[a-zA-Z0-9_\.\-]+$", clean_name):
            raise ValueError("Invalid filename: contains unsupported characters or path traversal.")

        ext = Path(clean_name).suffix.lower()
        if ext not in ALLOWED_EXTENSIONS:
            raise ValueError(f"Invalid file extension {ext}. Allowed: {list(ALLOWED_EXTENSIONS)}")

        repo_root = Path(__file__).parent.parent.resolve()
        target_dir = (repo_root / ALLOWED_SOURCE_TYPES[source_type]).resolve()

        # Path traversal guard relative to repository root
        target_dir.relative_to(repo_root)

        target_file = (target_dir / clean_name).resolve()
        target_file.relative_to(target_dir)

        return target_file

    def write_uploaded_file(self, source_type: str, filename: str, content: bytes) -> str:
        target_file = self.safe_import_path(source_type, filename)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        target_file.write_bytes(content)
        repo_root = Path(__file__).parent.parent.resolve()
        return str(target_file.relative_to(repo_root)).replace("\\", "/")

    async def handle_api(self, method: str, path: str, body: Optional[dict], files: Optional[dict] = None) -> Tuple[int, dict]:
        parsed_url = urllib.parse.urlparse(path)
        route = parsed_url.path
        query_params = urllib.parse.parse_qs(parsed_url.query)

        try:
            if route == "/api/health":
                return (200, {
                    "ok": True,
                    "service": "dana-training-web-console",
                    "safety": {
                        "no_auto_approval": True,
                        "no_prompt_edits": True,
                        "no_provider_uploads": True,
                        "no_fine_tuning_started": True,
                        "no_deployment": True,
                        "human_review_required": True
                    }
                })

            elif route == "/api/safety":
                return (200, {
                    "no_auto_approval": True,
                    "no_prompt_edits": True,
                    "no_provider_uploads": True,
                    "no_fine_tuning_started": True,
                    "no_deployment": True,
                    "human_review_required": True
                })

            elif route == "/api/summary":
                summary = await self.console.get_summary()
                return (200, summary.model_dump(mode="json"))

            elif route == "/api/review-items":
                status = query_params.get("status", ["pending"])[0]
                item_type = query_params.get("type", [None])[0] or query_params.get("item_type", [None])[0]
                limit = int(query_params.get("limit", [50])[0])
                res = await self.console.list_review_items(status=status, item_type=item_type, limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route.startswith("/api/review-items/"):
                parts = route.split("/")
                if len(parts) == 4:
                    item_id = parts[3]
                    res = await self.console.show_review_item(item_id)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 5 and parts[4] in ("approve", "reject", "needs-changes"):
                    item_id = parts[3]
                    action = parts[4]
                    reviewer = body.get("reviewer") if body else None
                    notes = body.get("notes") if body else None

                    if action == "approve":
                        res = await self.console.approve_review_item(item_id, reviewer, notes)
                    elif action == "reject":
                        res = await self.console.reject_review_item(item_id, reviewer, notes)
                    else:
                        res = await self.console.request_review_changes(item_id, reviewer, notes)

                    return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/intake/folder":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.run_intake(
                    mode="folder",
                    path=body.get("path"),
                    source_type=body.get("source_type"),
                    daily_qa=bool(body.get("daily_qa", False)),
                    dry_run=bool(body.get("dry_run", False)),
                    limit=body.get("limit"),
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/intake/manifest":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.run_intake(
                    mode="manifest",
                    manifest_path=body.get("manifest_path"),
                    dry_run=bool(body.get("dry_run", False)),
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/intake/daily":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.run_intake(
                    mode="daily",
                    daily_qa=bool(body.get("daily_qa", True)),
                    dry_run=bool(body.get("dry_run", False)),
                    limit=body.get("limit"),
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/scheduler/once":
                daily_qa = bool(body.get("daily_qa", True)) if body else True
                dry_run = bool(body.get("dry_run", False)) if body else False
                limit = body.get("limit") if body else None
                res = await self.console.run_scheduler_once(
                    daily_qa=daily_qa,
                    dry_run=dry_run,
                    limit=limit,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/youtube/import":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.import_youtube(
                    content=body.get("content"),
                    title=body.get("title"),
                    source_url=body.get("source_url"),
                    run_intake=bool(body.get("run_intake", True)),
                    dry_run=bool(body.get("dry_run", False)),
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/readiness":
                strict = bool(body.get("strict", True)) if body else True
                fail_on_medium = bool(body.get("fail_on_medium", False)) if body else False
                res = self.console.run_readiness(
                    strict=strict,
                    fail_on_medium=fail_on_medium,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/reports":
                rtype = query_params.get("type", [None])[0] or query_params.get("report_type", [None])[0]
                limit = int(query_params.get("limit", [50])[0])
                res = self.console.list_reports(report_type=rtype, limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/report":
                rpath = query_params.get("path", [None])[0]
                if not rpath:
                    return (400, {"success": False, "error": "Query parameter 'path' is required."})
                res = self.console.read_report(rpath)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/upload":
                if not files or "file" not in files:
                    return (400, {"success": False, "error": "No file uploaded."})
                source_type = body.get("source_type") if body else None
                if not source_type:
                    return (400, {"success": False, "error": "source_type parameter is required."})

                filename, content = files["file"]
                saved_rel_path = self.write_uploaded_file(source_type, filename, content)
                return (200, {
                    "success": True,
                    "message": f"File {filename} uploaded successfully.",
                    "data": {
                        "filename": filename,
                        "source_type": source_type,
                        "path": saved_rel_path
                    }
                })

            # =================================================================
            # Advanced Training Workflow APIs (Prompt 27)
            # =================================================================

            # A. QA & Evals
            elif route == "/api/qa/daily":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.run_daily_qa(
                    date=body.get("date"),
                    date_from=body.get("date_from"),
                    date_to=body.get("date_to"),
                    dry_run=bool(body.get("dry_run", False)),
                    limit=body.get("limit"),
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/evals/run":
                case_id = body.get("case_id") if body else None
                stage = body.get("stage") if body else None
                objection = body.get("objection") if body else None
                limit = body.get("limit") if body else None
                res = await self.console.run_eval_cases(
                    case_id=case_id,
                    stage=stage,
                    objection=objection,
                    limit=limit,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/replay/run":
                fixture = body.get("fixture") if body else None
                fixture_dir = body.get("fixture_dir") if body else None
                mode = body.get("mode", "static") if body else "static"
                fail_fast = bool(body.get("fail_fast", False)) if body else False
                res = await self.console.run_transcript_replay(
                    fixture=fixture,
                    fixture_dir=fixture_dir,
                    mode=mode,
                    fail_fast=fail_fast,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/simulations/run":
                persona = body.get("persona") if body else None
                run_all = bool(body.get("run_all", False)) if body else False
                res = await self.console.run_prospect_simulations(
                    persona=persona,
                    run_all=run_all,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            # B. Prompt
            elif route == "/api/prompt/versions":
                limit = int(query_params.get("limit", [50])[0])
                res = await self.console.list_prompt_versions(limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/prompt/patches/generate":
                dry_run = bool(body.get("dry_run", False)) if body else False
                limit = body.get("limit") if body else None
                res = await self.console.generate_prompt_patches(
                    dry_run=dry_run,
                    limit=limit,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/prompt/patches/preview":
                patch_id = body.get("patch_id") if body else None
                approved_only = bool(body.get("approved_only", True)) if body else True
                create_candidate = bool(body.get("create_candidate_version", False)) if body else False
                skip_gates = bool(body.get("skip_gates", False)) if body else False
                res = await self.console.preview_prompt_patches(
                    patch_id=patch_id,
                    approved_only=approved_only,
                    create_candidate_version=create_candidate,
                    skip_gates=skip_gates,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            # C. Canary
            elif route.startswith("/api/canary/"):
                parts = route.split("/")
                if len(parts) == 4 and parts[3] == "list":
                    limit = int(query_params.get("limit", [50])[0])
                    res = await self.console.list_canaries(limit=limit)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 4 and parts[3] == "check-candidate":
                    if not body:
                        return (400, {"success": False, "error": "JSON body is required."})
                    pvid = body.get("prompt_version_id")
                    if not pvid:
                        return (400, {"success": False, "error": "prompt_version_id is required."})
                    res = await self.console.check_canary_candidate(pvid)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 4 and parts[3] == "create":
                    if not body:
                        return (400, {"success": False, "error": "JSON body is required."})
                    pvid = body.get("prompt_version_id")
                    traffic = float(body.get("traffic_percent", 1.0))
                    operator = body.get("operator")
                    notes = body.get("notes")
                    if not pvid:
                        return (400, {"success": False, "error": "prompt_version_id is required."})
                    if not operator:
                        return (400, {"success": False, "error": "operator is required."})
                    res = await self.console.create_canary_plan(pvid, traffic, operator, notes)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 4 and parts[3] == "monitor":
                    exp_id = body.get("experiment_id") if body else None
                    res = await self.console.monitor_canary(exp_id)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 5:
                    experiment_id = parts[3]
                    action = parts[4]
                    if not body:
                        return (400, {"success": False, "error": "JSON body is required."})
                    operator = body.get("operator")
                    notes = body.get("notes") or body.get("reason")
                    
                    if not operator:
                        return (400, {"success": False, "error": "operator is required."})
                    if action in ("rollback", "cancel") and not notes:
                        return (400, {"success": False, "error": "notes/reason is required for rollback/cancel."})
                        
                    if action == "approve":
                        res = await self.console.approve_canary(experiment_id, operator, notes)
                    elif action == "start":
                        res = await self.console.start_canary(experiment_id, operator, notes)
                    elif action == "pause":
                        res = await self.console.pause_canary(experiment_id, operator, notes)
                    elif action == "rollback":
                        res = await self.console.rollback_canary(experiment_id, operator, notes)
                    elif action == "complete":
                        res = await self.console.complete_canary(experiment_id, operator, notes)
                    elif action == "cancel":
                        res = await self.console.cancel_canary(experiment_id, operator, notes)
                    else:
                        return (404, {"success": False, "error": f"Invalid canary action: {action}"})
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 4:
                    experiment_id = parts[3]
                    res = await self.console.show_canary(experiment_id)
                    return (200 if res.success else 400, res.model_dump(mode="json"))

            # D. Fine-tune
            elif route == "/api/fine-tune/export":
                dry_run = bool(body.get("dry_run", False)) if body else False
                limit = body.get("limit") if body else None
                stage = body.get("stage") if body else None
                objection = body.get("objection") if body else None
                res = await self.console.export_fine_tune_dataset(
                    dry_run=dry_run,
                    limit=limit,
                    stage=stage,
                    objection=objection,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/fine-tune/gate":
                if not body or not body.get("dataset_path"):
                    return (400, {"success": False, "error": "dataset_path is required."})
                strict = bool(body.get("strict", True))
                res = await self.console.gate_fine_tune_dataset(
                    dataset_path=body["dataset_path"],
                    strict=strict,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/fine-tune/job-request":
                if not body or not body.get("dataset_path"):
                    return (400, {"success": False, "error": "dataset_path is required."})
                gate_report = body.get("gate_report_path")
                provider = body.get("provider", "openai")
                dry_run = bool(body.get("dry_run", False))
                res = await self.console.prepare_fine_tune_job_request(
                    dataset_path=body["dataset_path"],
                    gate_report_path=gate_report,
                    provider=provider,
                    dry_run=dry_run,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/fine-tune/track":
                if not body or not body.get("job_request_id"):
                    return (400, {"success": False, "error": "job_request_id is required."})
                if not body.get("operator"):
                    return (400, {"success": False, "error": "operator is required."})
                status = body.get("status", "requested")
                operator = body["operator"]
                notes = body.get("notes")
                provider_job_id = body.get("provider_job_id")
                res = await self.console.track_fine_tune_job(
                    job_request_id=body["job_request_id"],
                    status=status,
                    operator=operator,
                    notes=notes,
                    provider_job_id=provider_job_id,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/fine-tune/tracking":
                limit = int(query_params.get("limit", [50])[0])
                res = await self.console.list_fine_tune_tracking(limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            # E. Post-call Export
            elif route == "/api/post-call/export":
                if not body or "payload" not in body:
                    return (400, {"success": False, "error": "payload is required."})
                payload = body["payload"]
                enabled = bool(body.get("enabled", True))
                run_intake = bool(body.get("run_intake", False))
                dry_run = bool(body.get("dry_run", False))
                res = await self.console.export_completed_call_payload(
                    payload=payload,
                    enabled=enabled,
                    run_intake=run_intake,
                    dry_run=dry_run,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            return (404, {"success": False, "error": f"API route not found: {route}"})

        except Exception as e:
            return (500, {"success": False, "error": "Internal Server Error", "message": str(e)})
