import csv
import uuid
import hashlib
import json
from pathlib import Path
from typing import Any, Optional, Dict, List, Tuple, Union
from pydantic import BaseModel, Field

from storage.repository import Repository


class LeadImportResult(BaseModel):
    """The result summary of a lead import run."""

    campaign_id: str
    total_rows: int = 0
    imported_count: int = 0
    duplicate_count: int = 0
    suppressed_count: int = 0
    failed_count: int = 0
    lead_ids: List[str] = Field(default_factory=list)
    warnings: List[str] = Field(default_factory=list)
    errors: List[Dict[str, Any]] = Field(default_factory=list)


class CampaignLeadImporter:
    """Imports leads from CSV or JSON files into outbound campaigns."""

    def __init__(self, repository: Repository | None = None) -> None:
        self.repository = repository or Repository()

    def normalize_phone(self, phone: str) -> str:
        """Normalize a phone number to E.164 format."""
        if not phone:
            raise ValueError("Phone number is empty")
        cleaned = "".join(c for c in phone if c.isdigit())
        if len(cleaned) == 10:
            return f"+1{cleaned}"
        elif len(cleaned) == 11 and cleaned.startswith("1"):
            return f"+{cleaned}"
        elif phone.startswith("+") and len(cleaned) >= 10:
            return f"+{cleaned}"
        elif len(cleaned) >= 10:
            return f"+{cleaned}"
        raise ValueError(f"Invalid phone number format: {phone}")

    def hash_phone(self, phone: str) -> str:
        """Hash normalized phone number to SHA256."""
        normalized = self.normalize_phone(phone)
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()

    def validate_lead(self, row: dict) -> tuple[bool, Optional[str]]:
        """Validate raw lead fields."""
        phone = row.get("phone_number") or row.get("phone") or row.get("phone_e164")
        if not phone:
            return False, "Missing phone number"
        try:
            self.normalize_phone(str(phone))
        except ValueError as e:
            return False, str(e)
        return True, None

    async def is_duplicate(self, campaign_id: str, phone_hash: str) -> bool:
        """Check if a phone hash is already imported under this campaign."""
        # Query leads matching campaign_id
        leads = await self.repository.query_campaign_leads({"campaign_id": campaign_id})
        for lead in leads:
            phone = lead.get("phone_number") or lead.get("phone_e164")
            if phone:
                try:
                    norm = self.normalize_phone(phone)
                    if hashlib.sha256(norm.encode("utf-8")).hexdigest() == phone_hash:
                        return True
                except Exception:
                    pass
        return False

    async def is_suppressed(self, phone_number: str) -> tuple[bool, Optional[str]]:
        """Check if a phone number is suppressed (DNC or Wrong Number)."""
        try:
            norm_phone = self.normalize_phone(phone_number)
        except ValueError:
            return False, None

        # 1. Scrub against dnc_requests collection
        dnc_records = await self.repository._store.query("dnc_requests", {"phone_e164": norm_phone})
        if dnc_records:
            return True, "DNC registry match"

        # 2. Check if any prior campaign lead was marked DNC or wrong number
        # We can query all campaign leads matching this phone number
        leads = await self.repository._store.query("campaign_leads", {"phone_number": norm_phone})
        for l in leads:
            status = l.get("status")
            if status in ("dnc", "wrong_number", "do_not_call", "suppressed"):
                return True, f"Prior lead status was {status}"

        # 3. Check call outcomes in call attempts
        attempts = await self.repository._store.query("call_attempts", {"phone_number_redacted": norm_phone})
        # Try hashing to match redacted phone or check hash
        phone_hash = hashlib.sha256(norm_phone.encode("utf-8")).hexdigest()
        all_attempts = await self.repository.list_recent_call_attempts(limit=1000)
        for att in all_attempts:
            if att.get("phone_number_hash") == phone_hash or att.get("phone_number_redacted") == norm_phone:
                outcome = att.get("outcome")
                if outcome in ("dnc", "wrong_number", "do_not_call"):
                    return True, f"Prior call outcome was {outcome}"

        return False, None

    async def import_rows(self, campaign_id: str, rows: list[dict]) -> LeadImportResult:
        """Import a list of dictionaries as leads."""
        result = LeadImportResult(campaign_id=campaign_id, total_rows=len(rows))

        # Check if campaign exists
        campaign = await self.repository.get_outbound_campaign(campaign_id)
        if not campaign:
            result.errors.append({"row": 0, "error": f"Campaign {campaign_id} not found"})
            result.failed_count = len(rows)
            return result

        for idx, row in enumerate(rows, start=1):
            is_valid, err_msg = self.validate_lead(row)
            if not is_valid:
                result.errors.append({"row": idx, "error": err_msg or "Validation failed"})
                result.failed_count += 1
                continue

            raw_phone = row.get("phone_number") or row.get("phone") or row.get("phone_e164")
            normalized = self.normalize_phone(str(raw_phone))
            phone_hash = self.hash_phone(normalized)

            # Check DNC suppression
            suppressed, reason = await self.is_suppressed(normalized)
            
            # Prepare metadata with phone_hash
            row_meta = row.get("metadata") or {}
            if isinstance(row_meta, str):
                try:
                    row_meta = json.loads(row_meta)
                except Exception:
                    row_meta = {}
            elif not isinstance(row_meta, dict):
                row_meta = {}
            row_meta["phone_hash"] = phone_hash

            if suppressed:
                result.suppressed_count += 1
                # Save lead as suppressed
                lead_id = str(uuid.uuid4())
                await self.repository.save_campaign_lead(
                    id=lead_id,
                    campaign_id=campaign_id,
                    first_name=row.get("first_name"),
                    last_name=row.get("last_name"),
                    phone_number=normalized,
                    state=row.get("state"),
                    timezone=row.get("timezone"),
                    status="suppressed",
                    suppression_reason=reason or "Scrubbed against suppression lists",
                    priority=int(row.get("priority", 0)),
                    metadata=row_meta,
                )
                continue

            # Check duplicates
            duplicate = await self.is_duplicate(campaign_id, phone_hash)
            if duplicate:
                result.duplicate_count += 1
                result.warnings.append(f"Row {idx}: Phone {normalized} is a duplicate in campaign {campaign_id}")
                continue

            # Valid new lead -> Save
            lead_id = str(uuid.uuid4())
            await self.repository.save_campaign_lead(
                id=lead_id,
                campaign_id=campaign_id,
                first_name=row.get("first_name"),
                last_name=row.get("last_name"),
                phone_number=normalized,
                state=row.get("state"),
                timezone=row.get("timezone"),
                status="new",
                priority=int(row.get("priority", 0)),
                metadata=row_meta,
            )
            result.imported_count += 1
            result.lead_ids.append(lead_id)

        # Log CampaignControlEvent
        if result.imported_count > 0:
            await self.repository.save_campaign_control_event(
                campaign_id=campaign_id,
                event_type="lead_imported",
                operator="system",
                reason=f"Imported {result.imported_count} leads successfully",
                metadata={"imported": result.imported_count, "duplicates": result.duplicate_count, "suppressed": result.suppressed_count},
            )

        return result

    async def import_file(self, campaign_id: str, path: Union[str, Path]) -> LeadImportResult:
        """Parse file (CSV or JSON) and import leads."""
        file_path = Path(path)
        if not file_path.exists():
            res = LeadImportResult(campaign_id=campaign_id)
            res.errors.append({"file": str(path), "error": "File does not exist"})
            return res

        rows = []
        suffix = file_path.suffix.lower()

        try:
            if suffix == ".csv":
                with open(file_path, mode="r", encoding="utf-8-sig") as f:
                    reader = csv.DictReader(f)
                    for r in reader:
                        rows.append(dict(r))
            elif suffix == ".json":
                with open(file_path, mode="r", encoding="utf-8") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        # Support standard wrap format
                        rows = data.get("leads", [])
                    elif isinstance(data, list):
                        rows = data
                    else:
                        raise ValueError("Invalid JSON structure")
            else:
                res = LeadImportResult(campaign_id=campaign_id)
                res.errors.append({"file": str(path), "error": "Unsupported file format. Use .csv or .json"})
                return res
        except Exception as e:
            res = LeadImportResult(campaign_id=campaign_id)
            res.errors.append({"file": str(path), "error": f"Failed to parse file: {e}"})
            return res

        return await self.import_rows(campaign_id, rows)
