import os
import re
import json
import logging
import httpx
from pathlib import Path
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field

from storage.repository import Repository
from telephony.did_pool import DIDPoolManager
from storage.schemas import CallerIdNumber

logger = logging.getLogger(__name__)

BASE_URL = "https://api.telnyx.com/v2"


class TelnyxInventoryConfig(BaseModel):
    """Configuration options for the Telnyx DID inventory sync."""

    api_key: Optional[str] = None
    provider: str = "telnyx"
    sync_status: str = "active"  # active|paused
    default_daily_cap: int = 100
    default_hourly_cap: int = 20
    require_e164: bool = True
    dry_run: bool = False
    output_dir: str = "data/telephony_reports"


class TelnyxNumberRecord(BaseModel):
    """Individual phone number details returned from the Telnyx API."""

    phone_number: str
    friendly_name: Optional[str] = None
    status: Optional[str] = None
    connection_id: Optional[str] = None
    messaging_profile_id: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)


class TelnyxInventorySyncResult(BaseModel):
    """Result summary of a Telnyx DID inventory sync operation."""

    success: bool = False
    dry_run: bool
    fetched_count: int = 0
    imported_count: int = 0
    updated_count: int = 0
    skipped_count: int = 0
    failed_count: int = 0
    numbers: List[Dict[str, Any]] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[str] = Field(default_factory=list)
    report_json_path: Optional[str] = None
    report_markdown_path: Optional[str] = None


class TelnyxInventoryClient:
    """Telnyx API client for querying owned phone numbers."""

    def __init__(self, api_key: str | None = None) -> None:
        self.api_key = api_key

    def _get_headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ValueError("TELNYX_API_KEY is not configured.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

    async def list_owned_phone_numbers(self) -> List[TelnyxNumberRecord]:
        """Fetch all phone numbers owned by the account from Telnyx API."""
        if not self.api_key:
            raise ValueError("TELNYX_API_KEY is required to list phone numbers.")

        url = f"{BASE_URL}/phone_numbers"
        records: List[TelnyxNumberRecord] = []
        current_url = url

        async with httpx.AsyncClient() as client:
            headers = self._get_headers()
            while current_url:
                try:
                    res = await client.get(current_url, headers=headers)
                    if res.status_code != 200:
                        err_msg = f"Telnyx API error listing phone numbers (Status {res.status_code}): {res.text}"
                        err_msg = err_msg.replace(self.api_key, "TELNYX_API_KEY_REDACTED")
                        raise RuntimeError(err_msg)

                    body = res.json()
                    data = body.get("data", [])
                    for item in data:
                        phone_number = item.get("phone_number")
                        if not phone_number:
                            continue

                        records.append(
                            TelnyxNumberRecord(
                                phone_number=phone_number,
                                friendly_name=item.get("friendly_name") or item.get("connection_name"),
                                status=item.get("status"),
                                connection_id=item.get("connection_id"),
                                messaging_profile_id=item.get("messaging_profile_id"),
                                tags=item.get("tags") or [],
                                metadata=item,
                            )
                        )

                    links = body.get("links", {})
                    next_link = links.get("next")
                    if next_link:
                        if next_link.startswith("/"):
                            current_url = f"https://api.telnyx.com{next_link}"
                        else:
                            current_url = next_link
                    else:
                        current_url = None
                except Exception as e:
                    err_msg = str(e).replace(self.api_key, "TELNYX_API_KEY_REDACTED")
                    raise RuntimeError(f"HTTP error listing owned phone numbers: {err_msg}")

        return records


class TelnyxDIDInventorySyncService:
    """Coordinates Telnyx client fetch and merges results into local storage."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository or Repository()
        self.pool_manager = DIDPoolManager(self.repository)

    async def sync(self, config: TelnyxInventoryConfig) -> TelnyxInventorySyncResult:
        result = TelnyxInventorySyncResult(dry_run=config.dry_run)

        # Resolve API Key securely
        api_key = config.api_key
        if not api_key:
            from config.runtime_env import get_runtime_env
            try:
                env = get_runtime_env()
                api_key = env.get("telnyx_api_key")
            except Exception as e:
                result.errors.append(f"Failed to resolve environment: {str(e)}")
                result.success = False
                return result

        if not api_key:
            result.errors.append("TELNYX_API_KEY is not configured in the environment.")
            result.success = False
            return result

        # Fetch numbers
        client = TelnyxInventoryClient(api_key=api_key)
        try:
            fetched = await client.list_owned_phone_numbers()
            result.fetched_count = len(fetched)
        except Exception as e:
            err_msg = str(e).replace(api_key, "TELNYX_API_KEY_REDACTED")
            result.errors.append(f"Failed to fetch phone numbers from Telnyx API: {err_msg}")
            result.success = False
            return result

        # E.164 pattern: optional +, followed by 10 to 15 digits
        e164_pattern = re.compile(r"^\+[1-9]\d{1,14}$")

        for record in fetched:
            num_str = record.phone_number.strip()

            if config.require_e164 and not e164_pattern.match(num_str):
                result.skipped_count += 1
                result.warnings.append(f"Skipped invalid E.164 phone number: {num_str}")
                continue

            try:
                existing = await self.repository.get_did_by_number(num_str)

                if config.dry_run:
                    if existing:
                        result.updated_count += 1
                    else:
                        result.imported_count += 1
                else:
                    if existing:
                        merged_meta = existing.get("metadata") or {}
                        merged_meta.update(record.metadata)

                        existing.update({
                            "provider": "telnyx",
                            "verified_for_provider": True,
                            "daily_cap": config.default_daily_cap,
                            "hourly_cap": config.default_hourly_cap,
                            "status": config.sync_status,
                            "metadata": merged_meta,
                            "source": "telnyx_api",
                            "updated_at": datetime.now(timezone.utc),
                        })
                        await self.repository.save_did(**existing)
                        result.updated_count += 1
                    else:
                        await self.pool_manager.add_number(
                            provider="telnyx",
                            phone_number=num_str,
                            source="telnyx_api",
                            verified_for_provider=True,
                            status=config.sync_status,
                            daily_cap=config.default_daily_cap,
                            hourly_cap=config.default_hourly_cap,
                            metadata=record.metadata,
                        )
                        result.imported_count += 1

                result.numbers.append({
                    "phone_number": num_str,
                    "friendly_name": record.friendly_name,
                    "status": record.status,
                    "connection_id": record.connection_id,
                })
            except Exception as e:
                result.failed_count += 1
                result.errors.append(f"Failed to process number {num_str}: {str(e)}")

        result.success = (result.failed_count == 0 and len(result.errors) == 0)

        # Write reports
        try:
            self.write_reports(result, config.output_dir)
        except Exception as e:
            result.warnings.append(f"Failed to write reports to {config.output_dir}: {str(e)}")

        return result

    def write_reports(self, result: TelnyxInventorySyncResult, output_dir: str):
        out_path = Path(output_dir)
        out_path.mkdir(parents=True, exist_ok=True)

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%SZ")

        # 1. JSON Report
        json_file = out_path / f"telnyx_sync_report_{timestamp}.json"
        report_data = result.model_dump()
        with open(json_file, "w", encoding="utf-8") as f:
            json.dump(report_data, f, indent=2, default=str)
        result.report_json_path = str(json_file.resolve())

        # 2. Markdown Report
        md_file = out_path / f"telnyx_sync_report_{timestamp}.md"

        md_lines = [
            "# Telnyx DID Inventory Sync Report",
            f"**Timestamp**: {datetime.now(timezone.utc).isoformat()}",
            f"**Status**: {'SUCCESS' if result.success else 'FAILED'}",
            f"**Dry Run**: {result.dry_run}",
            "",
            "## Summary Metrics",
            f"- **Fetched**: {result.fetched_count}",
            f"- **Imported**: {result.imported_count}",
            f"- **Updated**: {result.updated_count}",
            f"- **Skipped**: {result.skipped_count}",
            f"- **Failed**: {result.failed_count}",
            "",
        ]

        if result.errors:
            md_lines.append("## Errors")
            for err in result.errors:
                md_lines.append(f"- {err}")
            md_lines.append("")

        if result.warnings:
            md_lines.append("## Warnings")
            for warn in result.warnings:
                md_lines.append(f"- {warn}")
            md_lines.append("")

        md_lines.append("## Phone Numbers Sync List")
        if result.numbers:
            md_lines.append("| Phone Number | Friendly Name | Connection ID | Status |")
            md_lines.append("|---|---|---|---|")
            for num in result.numbers:
                md_lines.append(
                    f"| {num.get('phone_number')} | {num.get('friendly_name') or 'N/A'} | {num.get('connection_id') or 'N/A'} | {num.get('status') or 'N/A'} |"
                )
        else:
            md_lines.append("No phone numbers processed.")

        with open(md_file, "w", encoding="utf-8") as f:
            f.write("\n".join(md_lines))
        result.report_markdown_path = str(md_file.resolve())
