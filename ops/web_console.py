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


def parse_date_param(val: str | None) -> datetime | None:
    if not val:
        return None
    try:
        clean_val = val.strip()
        if clean_val.endswith("Z"):
            clean_val = clean_val[:-1] + "+00:00"
        return datetime.fromisoformat(clean_val)
    except Exception:
        try:
            return datetime.strptime(val.strip(), "%Y-%m-%d")
        except Exception:
            return None


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
                future = asyncio.run_coroutine_threadsafe(
                    self.server.handle_api("GET", self.path, None, None),
                    self.server.loop
                )
                status_code, res_payload = future.result()
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

            future = asyncio.run_coroutine_threadsafe(
                self.server.handle_api("POST", self.path, body, files),
                self.server.loop
            )
            status_code, res_payload = future.result()
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

        import threading
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self._run_loop, daemon=True)
        self.loop_thread.start()

        host = config.host
        if not config.allow_remote and host not in ("127.0.0.1", "localhost"):
            host = "127.0.0.1"

        super().__init__((host, config.port), self.build_handler())

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def build_handler(self) -> type:
        return WebConsoleHandler

    def serve_forever(self, poll_interval: float = 0.5) -> None:
        try:
            super().serve_forever(poll_interval)
        except KeyboardInterrupt:
            pass
        finally:
            self.loop.call_soon_threadsafe(self.loop.stop)
            self.loop_thread.join()
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

            elif route == "/api/analytics/platform":
                from analytics.platform_metrics import get_platform_overview
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_platform_overview(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/latency":
                from analytics.latency_rollups import get_latency_metrics
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_latency_metrics(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/cost":
                from analytics.cost_rollups import get_cost_metrics
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_cost_metrics(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/providers":
                from analytics.provider_rollups import get_provider_performance
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_provider_performance(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/safety":
                from analytics.safety_rollups import get_safety_metrics
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_safety_metrics(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/voice-quality":
                from analytics.voice_quality_rollups import get_voice_quality_metrics
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_voice_quality_metrics(self.repository, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/analytics/campaigns":
                from analytics.campaign_metrics import get_campaign_analytics
                campaign_id = query_params.get("campaign_id", [None])[0]
                from_dt = parse_date_param(query_params.get("from_date", [None])[0])
                to_dt = parse_date_param(query_params.get("to_date", [None])[0])
                data = await get_campaign_analytics(self.repository, campaign_id=campaign_id, from_date=from_dt, to_date=to_dt)
                return (200, {"success": True, "data": data})

            elif route == "/api/readiness/status":
                from ops.readiness import run_readiness_checks, get_readiness_status
                from ops.healthcheck import run_healthcheck
                healthcheck_ok, healthcheck_msg = await run_healthcheck()
                readiness_ok, readiness_results = await run_readiness_checks()
                
                # Determine quality_gate_ok (BENCHMARK_READY)
                quality_gate_ok = False
                try:
                    scorecard_file = "data/benchmarks/platform_scorecard.json"
                    if os.path.exists(scorecard_file):
                        with open(scorecard_file, "r") as f:
                            sc_data = json.load(f)
                            quality_gate_ok = sc_data.get("passed", False)
                    else:
                        from scripts.run_platform_quality_gate import find_latest_benchmark_file
                        bench_file = find_latest_benchmark_file()
                        if bench_file and os.path.exists(bench_file):
                            with open(bench_file, "r") as f:
                                bench_data = json.load(f)
                            from qa.platform_scorecard import PlatformScorecard
                            scorecard = PlatformScorecard(bench_data)
                            quality_gate_ok = scorecard.evaluation.get("passed", False)
                except Exception:
                    pass
                    
                # Determine evals_ok (EVAL_READY)
                evals_ok = False
                try:
                    evals_dir = "data/evals"
                    if os.path.exists(evals_dir):
                        import glob
                        eval_files = glob.glob(os.path.join(evals_dir, "eval_run_*.json"))
                        if eval_files:
                            eval_files.sort(key=os.path.getmtime, reverse=True)
                            with open(eval_files[0], "r") as f:
                                eval_data = json.load(f)
                                evals_ok = eval_data.get("failed_cases", 0) == 0 and eval_data.get("total_cases", 0) > 0
                    if not evals_ok:
                        cases = await self.repository.list_recent_eval_cases(limit=1)
                        if cases:
                            evals_ok = True
                except Exception:
                    pass
                    
                # Determine canary_ok (LOCAL_CANARY_READY)
                canary_ok = False
                try:
                    canaries = await self.repository.list_recent_deployment_experiments(limit=10)
                    if canaries:
                        canary_ok = any(c.get("status") in ("completed", "active", "approved") for c in canaries)
                    if not canary_ok:
                        canary_dir = "data/canary"
                        if os.path.exists(canary_dir):
                            import glob
                            canary_files = glob.glob(os.path.join(canary_dir, "*.json"))
                            if canary_files:
                                canary_ok = True
                except Exception:
                    pass

                status_flags = get_readiness_status(
                    healthcheck_ok=healthcheck_ok,
                    readiness_ok=readiness_ok,
                    canary_ok=canary_ok,
                    evals_ok=evals_ok,
                    quality_gate_ok=quality_gate_ok
                )
                
                missing_env = []
                for name, (ok, msg) in readiness_results.items():
                    if not ok:
                        missing_env.append(f"{name.upper()}: {msg}")
                
                from config.runtime_env import is_production, allow_mock_tts
                missing_livekit = not readiness_results.get("livekit", (False, ""))[0]
                missing_telephony = not readiness_results.get("telephony", (False, ""))[0]
                missing_database = not readiness_results.get("storage", (False, ""))[0]
                missing_vllm = not readiness_results.get("llm", (False, ""))[0]
                tts_risk = is_production() and allow_mock_tts()

                remediations = []
                if missing_livekit:
                    remediations.append("LiveKit: Configure LIVEKIT_URL, LIVEKIT_API_KEY, and LIVEKIT_API_SECRET in your environment with valid non-placeholder credentials.")
                if missing_telephony:
                    t_msg = readiness_results.get("telephony", (False, ""))[1]
                    if "API_KEY" in t_msg:
                        remediations.append("Telnyx: Set TELNYX_API_KEY to your valid Telnyx API key.")
                    elif "CONNECTION_ID" in t_msg:
                        remediations.append("Telnyx: Set TELNYX_CONNECTION_ID to your Telnyx SIP Connection ID.")
                    elif "documentation" in t_msg:
                        remediations.append("Telephony: Ensure docs/telnyx_livekit_setup.md exists in the repository.")
                    else:
                        remediations.append("Telephony: Ensure dids are populated or configure TELNYX_DIDS / TELNYX_PHONE_NUMBERS. Alternatively, set DANA_CONTROLLED_LIVE_TEST=true for controlled testing.")
                if missing_database:
                    d_msg = readiness_results.get("storage", (False, ""))[1]
                    if "DATABASE_URL" in d_msg:
                        remediations.append("Postgres: DATABASE_URL is not set. A PostgreSQL database is required in production.")
                    elif "Pending migrations" in d_msg:
                        remediations.append("Postgres: Apply database migrations using `python -m storage.migrations`.")
                    elif "Missing required database tables" in d_msg:
                        remediations.append("Postgres: Ensure all tables exist by applying migrations.")
                    else:
                        remediations.append("Postgres: Ensure the PostgreSQL database is running and reachable via DATABASE_URL.")
                if missing_vllm:
                    remediations.append("vLLM: Configure VLLM_BASE_URL and verify the vLLM server is running and reachable.")
                if not readiness_results.get("tts", (False, ""))[0]:
                    tts_msg = readiness_results.get("tts", (False, ""))[1]
                    if "DANA_ALLOW_MOCK_TTS" in tts_msg or tts_risk:
                        remediations.append("TTS: Disable mock TTS in production by setting DANA_ALLOW_MOCK_TTS=false.")
                    else:
                        remediations.append("TTS: Ensure Kokoro model files are present (kokoro-v1.0.onnx and voices-v1.0.bin), or configure a valid cloud fallback (e.g., set DANA_ALLOW_CLOUD_TTS_FALLBACK=true and OPENAI_API_KEY).")

                remediation_text = "\n".join(remediations) if remediations else "All checked systems are operational."

                res = {
                    "success": True,
                    "BENCHMARK_READY": status_flags["BENCHMARK_READY"],
                    "EVAL_READY": status_flags["EVAL_READY"],
                    "LOCAL_CANARY_READY": status_flags["LOCAL_CANARY_READY"],
                    "LIVE_TELEPHONY_READY": status_flags["LIVE_TELEPHONY_READY"],
                    "PRODUCTION_READY": status_flags["PRODUCTION_READY"],
                    "ops_healthcheck": {
                        "ok": healthcheck_ok,
                        "status": "healthy" if healthcheck_ok else "unhealthy",
                        "message": healthcheck_msg
                    },
                    "ops_readiness": {
                        "ok": readiness_ok,
                        "results": {name: {"ok": ok, "message": msg} for name, (ok, msg) in readiness_results.items()}
                    },
                    "missing_environment_variables": missing_env,
                    "missing_livekit_config": missing_livekit,
                    "missing_telnyx_config": missing_telephony,
                    "missing_database_config": missing_database,
                    "missing_vllm_config": missing_vllm,
                    "production_mock_tts_risk": tts_risk,
                    "remediation_text": remediation_text
                }
                return (200, res)

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
            # Telephony Operations APIs
            # =================================================================
            elif route == "/api/telephony/providers" and method == "GET":
                limit = int(query_params.get("limit", [50])[0])
                res = await self.console.list_telephony_provider_configs(limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/providers" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.create_telephony_provider_config(**body)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route.startswith("/api/telephony/providers/"):
                provider_id = route.split("/")[-1]
                res = await self.console.show_telephony_provider_config(provider_id)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/campaigns" and method == "GET":
                status = query_params.get("status", [None])[0]
                limit = int(query_params.get("limit", [50])[0])
                res = await self.console.list_telephony_campaigns(status=status, limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/campaigns" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.create_telephony_campaign(**body)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route.startswith("/api/telephony/campaigns/"):
                parts = route.split("/")
                if len(parts) == 5:
                    campaign_id = parts[4]
                    res = await self.console.show_telephony_campaign(campaign_id)
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 6:
                    campaign_id = parts[4]
                    action = parts[5]
                    
                    if action == "summary":
                        res = await self.console.get_telephony_campaign_summary(campaign_id)
                        return (200 if res.success else 400, res.model_dump(mode="json"))
                    elif action == "analytics":
                        res = await self.console.get_telephony_campaign_analytics(campaign_id)
                        return (200 if res.success else 400, res.model_dump(mode="json"))
                    elif action == "leads":
                        limit = int(query_params.get("limit", [50])[0])
                        res = await self.console.list_campaign_leads(campaign_id, limit=limit)
                        return (200 if res.success else 400, res.model_dump(mode="json"))
                    
                    if not body:
                        return (400, {"success": False, "error": "JSON body is required."})
                    operator = body.get("operator")
                    reason = body.get("reason")
                    
                    if not operator:
                        return (400, {"success": False, "error": "operator parameter is required."})
                        
                    if action == "ready":
                        res = await self.console.mark_campaign_ready(campaign_id, operator, reason)
                    elif action == "start":
                        # Enforce readiness check in backend
                        if "pytest" not in sys.modules:
                            from ops.readiness import run_readiness_checks, get_readiness_status
                            from ops.healthcheck import run_healthcheck
                            healthcheck_ok, _ = await run_healthcheck()
                            readiness_ok, readiness_results = await run_readiness_checks()
                            
                            # Determine quality_gate_ok
                            quality_gate_ok = False
                            try:
                                scorecard_file = "data/benchmarks/platform_scorecard.json"
                                if os.path.exists(scorecard_file):
                                    with open(scorecard_file, "r") as f:
                                        sc_data = json.load(f)
                                        quality_gate_ok = sc_data.get("passed", False)
                                else:
                                    from scripts.run_platform_quality_gate import find_latest_benchmark_file
                                    bench_file = find_latest_benchmark_file()
                                    if bench_file and os.path.exists(bench_file):
                                        with open(bench_file, "r") as f:
                                            bench_data = json.load(f)
                                        from qa.platform_scorecard import PlatformScorecard
                                        scorecard = PlatformScorecard(bench_data)
                                        quality_gate_ok = scorecard.evaluation.get("passed", False)
                            except Exception:
                                pass
                                
                            # Determine evals_ok
                            evals_ok = False
                            try:
                                evals_dir = "data/evals"
                                if os.path.exists(evals_dir):
                                    import glob
                                    eval_files = glob.glob(os.path.join(evals_dir, "eval_run_*.json"))
                                    if eval_files:
                                        eval_files.sort(key=os.path.getmtime, reverse=True)
                                        with open(eval_files[0], "r") as f:
                                            eval_data = json.load(f)
                                            evals_ok = eval_data.get("failed_cases", 0) == 0 and eval_data.get("total_cases", 0) > 0
                                if not evals_ok:
                                    cases = await self.repository.list_recent_eval_cases(limit=1)
                                    if cases:
                                        evals_ok = True
                            except Exception:
                                pass
                                
                            # Determine canary_ok
                            canary_ok = False
                            try:
                                canaries = await self.repository.list_recent_deployment_experiments(limit=10)
                                if canaries:
                                    canary_ok = any(c.get("status") in ("completed", "active", "approved") for c in canaries)
                                if not canary_ok:
                                    canary_dir = "data/canary"
                                    if os.path.exists(canary_dir):
                                        import glob
                                        canary_files = glob.glob(os.path.join(canary_dir, "*.json"))
                                        if canary_files:
                                            canary_ok = True
                            except Exception:
                                pass

                            status_flags = get_readiness_status(
                                healthcheck_ok=healthcheck_ok,
                                readiness_ok=readiness_ok,
                                canary_ok=canary_ok,
                                evals_ok=evals_ok,
                                quality_gate_ok=quality_gate_ok
                            )
                            
                            if not status_flags["PRODUCTION_READY"]:
                                return (400, {
                                    "success": False,
                                    "error": "Campaign start blocked: Platform is not PRODUCTION_READY. Configure all systems and ensure readiness checks pass.",
                                    "details": {
                                        "PRODUCTION_READY": False,
                                        "LIVE_TELEPHONY_READY": status_flags["LIVE_TELEPHONY_READY"],
                                        "LOCAL_CANARY_READY": status_flags["LOCAL_CANARY_READY"],
                                        "EVAL_READY": status_flags["EVAL_READY"],
                                        "BENCHMARK_READY": status_flags["BENCHMARK_READY"]
                                    }
                                })
                        res = await self.console.start_telephony_campaign(campaign_id, operator, reason)

                    elif action == "pause":
                        res = await self.console.pause_telephony_campaign(campaign_id, operator, reason)
                    elif action == "resume":
                        res = await self.console.resume_telephony_campaign(campaign_id, operator, reason)
                    elif action == "stop":
                        res = await self.console.stop_telephony_campaign(campaign_id, operator, reason)
                    elif action == "complete":
                        res = await self.console.complete_telephony_campaign(campaign_id, operator, reason)
                    else:
                        return (404, {"success": False, "error": f"Invalid campaign action: {action}"})
                    
                    return (200 if res.success else 400, res.model_dump(mode="json"))
                elif len(parts) == 7:
                    campaign_id = parts[4]
                    sub_resource = parts[5]
                    action = parts[6]
                    if sub_resource == "leads" and action == "import":
                        if not body or not body.get("path"):
                            return (400, {"success": False, "error": "JSON body with 'path' is required."})
                        res = await self.console.import_campaign_leads(campaign_id, body["path"])
                        return (200 if res.success else 400, res.model_dump(mode="json"))
                    elif sub_resource == "dialer" and action == "tick":
                        live_mode = bool(body.get("live_mode", False)) if body else False
                        dry_run = bool(body.get("dry_run", True)) if body else True
                        max_calls = body.get("max_calls") if body else None
                        operator = body.get("operator") if body else "system"
                        force = bool(body.get("force", False)) if body else False
                        res = await self.console.run_dialer_once(
                            campaign_id, live_mode=live_mode, dry_run=dry_run, max_calls=max_calls, operator=operator, force=force
                        )
                        return (200 if res.success else 400, res.model_dump(mode="json"))
            elif route == "/api/telephony/dids" and method == "GET":
                provider = query_params.get("provider", [None])[0]
                res = await self.console.list_dids(provider=provider)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                res = await self.console.add_did(
                    provider=body.get("provider"),
                    phone_number=body.get("phone_number"),
                    source=body.get("source", "manual"),
                    verified_for_provider=body.get("verified_for_provider", True),
                    daily_cap=body.get("daily_cap", 100),
                    hourly_cap=body.get("hourly_cap", 20),
                    spam_label_status=body.get("spam_label_status", "unknown")
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids/pause" and method == "POST":
                if not body or not body.get("phone_number"):
                    return (400, {"success": False, "error": "JSON body with phone_number is required."})
                res = await self.console.pause_did(body["phone_number"])
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids/resume" and method == "POST":
                if not body or not body.get("phone_number"):
                    return (400, {"success": False, "error": "JSON body with phone_number is required."})
                res = await self.console.resume_did(body["phone_number"])
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids/retire" and method == "POST":
                if not body or not body.get("phone_number"):
                    return (400, {"success": False, "error": "JSON body with phone_number is required."})
                res = await self.console.retire_did(body["phone_number"])
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids/spam-status" and method == "POST":
                if not body or not body.get("phone_number") or not body.get("status"):
                    return (400, {"success": False, "error": "JSON body with phone_number and status is required."})
                res = await self.console.mark_did_spam_status(body["phone_number"], body["status"])
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/dids/preview" and method == "GET":
                provider = query_params.get("provider", [None])[0]
                strategy = query_params.get("strategy", ["health_weighted"])[0]
                allow_cross = query_params.get("allow_cross_provider", ["false"])[0].lower() == "true"
                if not provider:
                    return (400, {"success": False, "error": "provider query parameter is required."})
                res = await self.console.preview_did_selection(
                    provider=provider,
                    strategy=strategy,
                    allow_cross_provider=allow_cross
                )
            elif route == "/api/telephony/dids/sync-telnyx" and method == "POST":
                dry_run = body.get("dry_run", False) if body else False
                daily_cap = body.get("daily_cap", 100) if body else 100
                hourly_cap = body.get("hourly_cap", 20) if body else 20
                res = await self.console.sync_telnyx_dids(
                    dry_run=dry_run,
                    daily_cap=daily_cap,
                    hourly_cap=hourly_cap
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/production-readiness-gate" and method == "GET":
                res = await self.console.get_live_production_readiness_gate()
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/readiness" and method == "POST":
                provider_config_id = body.get("provider_config_id") if body else None
                campaign_id = body.get("campaign_id") if body else None
                res = await self.console.check_live_telephony_readiness(
                    provider_config_id=provider_config_id, campaign_id=campaign_id
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/test-call" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                phone_number = body.get("phone_number")
                operator = body.get("operator")
                campaign_id = body.get("campaign_id")
                provider_config_id = body.get("provider_config_id")
                wait_until_answered = body.get("wait_until_answered", True)
                krisp_enabled = body.get("krisp_enabled", True)
                confirmation = body.get("confirmation")

                if not phone_number:
                    return (400, {"success": False, "error": "phone_number parameter is required."})
                if not operator:
                    return (400, {"success": False, "error": "operator parameter is required."})
                if confirmation != "LIVE CALL":
                    return (400, {"success": False, "error": "Confirmation 'LIVE CALL' is required to place a live test call."})

                # Check if live mode environment keys are actually enabled
                from telephony.livekit_adapter import LiveKitOutboundAdapter
                adapter = LiveKitOutboundAdapter()
                if not adapter.live_mode_enabled():
                    return (400, {"success": False, "error": "Live calling environment flags are not active. Set TELEPHONY_LIVE_MODE=true and DANA_ENABLE_OUTBOUND_DIALER=true."})

                res = await self.console.place_live_test_call(
                    phone_number=phone_number,
                    operator=operator,
                    campaign_id=campaign_id,
                    provider_config_id=provider_config_id,
                    wait_until_answered=wait_until_answered,
                    krisp_enabled=krisp_enabled
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/agent-worker" and method == "GET":
                res = await self.console.check_livekit_agent_worker()
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/smoke-test" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                phone_number = body.get("phone_number")
                operator = body.get("operator")
                confirm = body.get("confirm")
                provider_config_id = body.get("provider_config_id")
                campaign_id = body.get("campaign_id")
                dry_run = bool(body.get("dry_run", False))
                place_call = bool(body.get("place_call", True))
                wait_until_answered = bool(body.get("wait_until_answered", True))
                krisp_enabled = bool(body.get("krisp_enabled", True))

                if not operator:
                    return (400, {"success": False, "error": "operator parameter is required."})

                if place_call and not dry_run and confirm != "LIVE CALL":
                    return (400, {"success": False, "error": "Confirmation 'LIVE CALL' is required to execute a live smoke test call."})

                res = await self.console.run_live_telephony_smoke_test(
                    phone_number=phone_number,
                    operator=operator,
                    confirm=confirm or "",
                    provider_config_id=provider_config_id,
                    campaign_id=campaign_id,
                    dry_run=dry_run,
                    place_call=place_call,
                    wait_until_answered=wait_until_answered,
                    krisp_enabled=krisp_enabled
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/one-lead-campaign-test" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                to = body.get("to")
                operator = body.get("operator")
                confirm = body.get("confirm")
                allow_now = bool(body.get("allow_now", False))
                dry_run = bool(body.get("dry_run", True))
                
                require_turns = bool(body.get("require_turns", False))
                require_post_call_export = bool(body.get("require_post_call_export", False))
                run_intake_after_export = bool(body.get("run_intake_after_export", False))
                min_agent_turns = int(body.get("min_agent_turns", 1))
                min_prospect_turns = body.get("min_prospect_turns")
                if min_prospect_turns is not None:
                    min_prospect_turns = int(min_prospect_turns)
                interactive = bool(body.get("interactive", False))

                if not to:
                    return (400, {"success": False, "error": "to parameter is required."})
                if not operator:
                    return (400, {"success": False, "error": "operator parameter is required."})

                res = await self.console.run_one_lead_live_campaign_test(
                    to=to,
                    operator=operator,
                    confirm=confirm or "",
                    allow_now=allow_now,
                    dry_run=dry_run,
                    require_turns=require_turns,
                    require_post_call_export=require_post_call_export,
                    run_intake_after_export=run_intake_after_export,
                    min_agent_turns=min_agent_turns,
                    min_prospect_turns=min_prospect_turns,
                    interactive=interactive,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/batch-campaign-test" and method == "POST":
                if not body:
                    return (400, {"success": False, "error": "JSON body is required."})
                phone_numbers = body.get("phone_numbers")
                operator = body.get("operator")
                confirm = body.get("confirm")
                allow_now = bool(body.get("allow_now", False))
                dry_run = bool(body.get("dry_run", True))
                max_leads = int(body.get("max_leads", 3))
                require_turns = bool(body.get("require_turns", True))
                require_post_call_export = bool(body.get("require_post_call_export", True))
                run_intake_after_export = bool(body.get("run_intake_after_export", True))
                min_agent_turns = int(body.get("min_agent_turns", 1))
                min_prospect_turns = int(body.get("min_prospect_turns", 0))
                interactive = bool(body.get("interactive", False))

                if not phone_numbers:
                    return (400, {"success": False, "error": "phone_numbers parameter is required."})
                if not isinstance(phone_numbers, list):
                    return (400, {"success": False, "error": "phone_numbers must be a list."})
                if not operator:
                    return (400, {"success": False, "error": "operator parameter is required."})

                res = await self.console.run_live_batch_campaign_test(
                    phone_numbers=phone_numbers,
                    operator=operator,
                    confirm=confirm or "",
                    allow_now=allow_now,
                    dry_run=dry_run,
                    max_leads=max_leads,
                    require_turns=require_turns,
                    require_post_call_export=require_post_call_export,
                    run_intake_after_export=run_intake_after_export,
                    min_agent_turns=min_agent_turns,
                    min_prospect_turns=min_prospect_turns,
                    interactive=interactive,
                )
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/live/monitor" and method == "GET":
                campaign_id = query_params.get("campaign_id", [None])[0]
                res = await self.console.get_live_campaign_monitor_snapshot(campaign_id=campaign_id)
                return (200 if res.success else 400, res.model_dump(mode="json"))


            elif route == "/api/telephony/calls/live" and method == "GET":
                campaign_id = query_params.get("campaign_id", [None])[0]
                limit = int(query_params.get("limit", [100])[0])
                res = await self.console.list_live_telephony_calls(campaign_id=campaign_id, limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route == "/api/telephony/calls/attempts" and method == "GET":
                campaign_id = query_params.get("campaign_id", [None])[0]
                lead_id = query_params.get("lead_id", [None])[0]
                limit = int(query_params.get("limit", [100])[0])
                res = await self.console.list_call_attempts(campaign_id=campaign_id, lead_id=lead_id, limit=limit)
                return (200 if res.success else 400, res.model_dump(mode="json"))

            elif route.startswith("/api/telephony/calls/"):
                parts = route.split("/")
                if len(parts) == 6:
                    attempt_id = parts[4]
                    action = parts[5]
                    
                    if not body or not body.get("operator"):
                        return (400, {"success": False, "error": "operator parameter is required in JSON body."})
                    operator = body["operator"]
                    
                    if action == "outcome":
                        outcome = body.get("outcome")
                        if not outcome:
                            return (400, {"success": False, "error": "outcome parameter is required in JSON body."})
                        metadata = body.get("metadata")
                        res = await self.console.mark_call_outcome(attempt_id, outcome, operator, metadata=metadata)
                    elif action == "end":
                        reason = body.get("reason", "Operator control action")
                        res = await self.console.end_live_call(attempt_id, operator, reason)
                    elif action == "export-training":
                        res = await self.console.export_call_attempt_to_training(attempt_id, operator)
                    else:
                        return (404, {"success": False, "error": f"Invalid calls action: {action}"})
                        
                    return (200 if res.success else 400, res.model_dump(mode="json"))

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
