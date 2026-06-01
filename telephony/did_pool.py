import os
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, List, Dict
from pydantic import BaseModel, Field

from storage.repository import Repository
from storage.schemas import CallerIdNumber, CallerIdSelectionConfig, CallerIdSelectionResult

class DIDPoolManager:
    """Manages owned/authorized caller IDs, cooldowns, and spam reputation."""

    def __init__(self, repository: Optional[Repository] = None) -> None:
        self.repository = repository or Repository()

    def load_numbers_from_env(self, provider: str) -> List[CallerIdNumber]:
        """Load candidate numbers for a provider from environment variables."""
        provider = provider.strip().lower()
        numbers_set = set()
        candidates: List[CallerIdNumber] = []

        # Read env variables by provider
        raw_vals = []
        if provider == "telnyx":
            raw_vals = [
                os.environ.get("DANA_OUTBOUND_CALLER_ID"),
                os.environ.get("TELNYX_OUTBOUND_CALLER_ID"),
                os.environ.get("TELNYX_DIDS"),
                os.environ.get("TELNYX_PHONE_NUMBERS"),
            ]
        elif provider == "bulkvs":
            raw_vals = [
                os.environ.get("BULKVS_OUTBOUND_CALLER_ID"),
                os.environ.get("BULKVS_DIDS"),
                os.environ.get("BULKVS_PHONE_NUMBERS"),
            ]
            # Include DANA_OUTBOUND_CALLER_ID only if explicitly allowed
            if os.environ.get("DANA_ALLOW_DANA_CALLER_ID_FOR_BULKVS", "").lower() == "true":
                raw_vals.append(os.environ.get("DANA_OUTBOUND_CALLER_ID"))
        elif provider == "signalwire":
            raw_vals = [
                os.environ.get("DANA_OUTBOUND_CALLER_ID"),
                os.environ.get("SIGNALWIRE_OUTBOUND_CALLER_ID"),
                os.environ.get("SIGNALWIRE_DIDS"),
            ]
        elif provider == "twilio":
            raw_vals = [
                os.environ.get("DANA_OUTBOUND_CALLER_ID"),
                os.environ.get("TWILIO_CALLER_ID"),
                os.environ.get("TWILIO_PHONE_NUMBERS"),
            ]
        elif provider == "mock":
            raw_vals = [
                os.environ.get("DANA_OUTBOUND_CALLER_ID"),
                os.environ.get("TELNYX_OUTBOUND_CALLER_ID"),
                os.environ.get("TELNYX_DIDS"),
                os.environ.get("TELNYX_PHONE_NUMBERS"),
                os.environ.get("SIGNALWIRE_DIDS"),
                os.environ.get("TWILIO_PHONE_NUMBERS"),
            ]

        # Parse comma-separated numbers
        for val in raw_vals:
            if not val:
                continue
            for part in val.split(","):
                phone = part.strip()
                if phone and phone not in numbers_set:
                    numbers_set.add(phone)
                    candidates.append(
                        CallerIdNumber(
                            provider=provider,
                            phone_number=phone,
                            source="env",
                            verified_for_provider=True,
                            status="active",
                        )
                    )
        
        # Add a default mock number if provider is mock and no env config exists
        if provider == "mock" and not candidates:
            candidates.append(
                CallerIdNumber(
                    provider="mock",
                    phone_number="+15550000",
                    source="env",
                    verified_for_provider=True,
                    status="active",
                )
            )
            
        return candidates

    async def list_numbers(self, provider: Optional[str] = None) -> List[CallerIdNumber]:
        """List all merged numbers from environment and database storage."""
        # 1. Fetch DB numbers
        db_dicts = await self.repository.list_dids(provider=provider)
        db_numbers = [CallerIdNumber(**d) for d in db_dicts]

        # 2. Fetch env numbers for all providers if provider is None
        providers = [provider] if provider else ["telnyx", "bulkvs", "signalwire", "twilio", "mock"]
        env_numbers = []
        for p in providers:
            env_numbers.extend(self.load_numbers_from_env(p))

        # 3. Merge: Database overrides env, and real providers override mock
        merged: Dict[str, CallerIdNumber] = {}
        for num in env_numbers:
            if num.phone_number in merged:
                existing = merged[num.phone_number]
                if existing.provider == "mock" and num.provider != "mock":
                    merged[num.phone_number] = num
                continue
            merged[num.phone_number] = num
        for num in db_numbers:
            merged[num.phone_number] = num

        return sorted(list(merged.values()), key=lambda x: x.phone_number)

    async def add_number(
        self,
        provider: str,
        phone_number: str,
        source: str = "manual",
        verified_for_provider: bool = True,
        **kwargs: Any
    ) -> CallerIdNumber:
        """Add/save a new CallerIdNumber to the database."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        
        did_data = {
            "provider": provider.strip().lower(),
            "phone_number": phone_number,
            "source": source,
            "verified_for_provider": verified_for_provider,
            "status": "active",
            **kwargs
        }
        
        if existing:
            did_data["id"] = existing["id"]
            
        did_id = await self.repository.save_did(**did_data)
        saved = await self.repository.get_did(did_id)
        return CallerIdNumber(**saved)

    async def pause_number(self, phone_number: str) -> bool:
        """Pause a number in the database."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        if not existing:
            # Create from env or save a new one
            all_numbers = await self.list_numbers()
            match = [n for n in all_numbers if n.phone_number == phone_number]
            if match:
                data = match[0].model_dump()
                data["status"] = "paused"
                await self.repository.save_did(**data)
                return True
            return False
        
        existing["status"] = "paused"
        existing["updated_at"] = datetime.now(timezone.utc)
        await self.repository.save_did(**existing)
        return True

    async def resume_number(self, phone_number: str) -> bool:
        """Resume a paused number in the database."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        if not existing:
            return False
        
        existing["status"] = "active"
        existing["updated_at"] = datetime.now(timezone.utc)
        await self.repository.save_did(**existing)
        return True

    async def retire_number(self, phone_number: str) -> bool:
        """Retire a number in the database."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        if not existing:
            all_numbers = await self.list_numbers()
            match = [n for n in all_numbers if n.phone_number == phone_number]
            if match:
                data = match[0].model_dump()
                data["status"] = "retired"
                await self.repository.save_did(**data)
                return True
            return False
        
        existing["status"] = "retired"
        existing["updated_at"] = datetime.now(timezone.utc)
        await self.repository.save_did(**existing)
        return True

    async def mark_spam_status(self, phone_number: str, status: str) -> bool:
        """Update the spam reputation status of a phone number."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        if not existing:
            all_numbers = await self.list_numbers()
            match = [n for n in all_numbers if n.phone_number == phone_number]
            if match:
                data = match[0].model_dump()
                data["spam_label_status"] = status
                await self.repository.save_did(**data)
                return True
            return False
        
        existing["spam_label_status"] = status
        existing["updated_at"] = datetime.now(timezone.utc)
        await self.repository.save_did(**existing)
        return True

    async def record_call_use(self, phone_number: str, outcome: Optional[str] = None) -> None:
        """Record usage of a phone number, incrementing daily/hourly counts."""
        phone_number = phone_number.strip()
        existing = await self.repository.get_did_by_number(phone_number)
        
        if not existing:
            all_numbers = await self.list_numbers()
            match = [n for n in all_numbers if n.phone_number == phone_number]
            if match:
                data = match[0].model_dump()
                existing = data
            else:
                # Add it as manual if not found anywhere
                existing = {
                    "provider": "telnyx",
                    "phone_number": phone_number,
                    "source": "manual",
                    "verified_for_provider": False,
                    "status": "active",
                }

        now = datetime.now(timezone.utc)
        
        # Reset counters if last_used_at is not today/this hour
        last_used_str = existing.get("last_used_at")
        calls_today = existing.get("calls_today", 0)
        calls_this_hour = existing.get("calls_this_hour", 0)
        
        if last_used_str:
            try:
                last_used = datetime.fromisoformat(last_used_str.replace("Z", "+00:00"))
                if last_used.date() != now.date():
                    calls_today = 0
                if last_used.hour != now.hour or last_used.date() != now.date():
                    calls_this_hour = 0
            except Exception:
                pass

        calls_today += 1
        calls_this_hour += 1

        existing["calls_today"] = calls_today
        existing["calls_this_hour"] = calls_this_hour
        existing["last_used_at"] = now
        existing["updated_at"] = now

        # Compute metrics based on outcome
        if outcome:
            complaint_count = existing.get("complaint_count", 0)
            dnc_count = existing.get("dnc_count", 0)
            if outcome == "dnc":
                dnc_count += 1
            elif outcome == "complaint":
                complaint_count += 1
            existing["dnc_count"] = dnc_count
            existing["complaint_count"] = complaint_count

        await self.repository.save_did(**existing)

    async def select_caller_id(self, config: CallerIdSelectionConfig) -> CallerIdSelectionResult:
        """Select a caller ID from the pool based on strategy and provider safety rules."""
        provider = config.provider.strip().lower()
        
        # 1. Fetch merged candidates list
        if config.allow_cross_provider:
            all_numbers = await self.list_numbers()
        else:
            all_numbers = await self.list_numbers(provider=provider)
        
        candidates: List[CallerIdNumber] = []
        warnings: List[str] = []
        
        # Filter candidates based on safety rules
        for num in all_numbers:
            # Ensure number provider checks are enforced
            if num.provider != provider:
                # Cross-provider restriction
                if not config.allow_cross_provider:
                    continue
                
                # SignalWire restriction
                if provider == "telnyx" and num.provider == "signalwire":
                    continue
                
                # BulkVS restriction for Telnyx provider
                if provider == "telnyx" and num.provider == "bulkvs" and not config.allow_cross_provider:
                    continue

                # Mock restriction: never mix mock with real providers
                if (num.provider == "mock") != (provider == "mock"):
                    continue
                
                warnings.append(
                    "Cross-provider caller ID may reduce attestation and increase call labeling risk."
                )

            candidates.append(num)

        candidate_count = len(candidates)
        eligible: List[CallerIdNumber] = []
        now = datetime.now(timezone.utc)

        # 2. Filter for eligibility
        for num in candidates:
            # Exclude status checks
            if num.status in ("paused", "blocked", "retired"):
                continue
            
            # Exclude cooldown
            if num.cooldown_until:
                try:
                    cooldown = num.cooldown_until
                    if isinstance(cooldown, str):
                        cooldown = datetime.fromisoformat(cooldown.replace("Z", "+00:00"))
                    if now < cooldown:
                        continue
                except Exception:
                    pass

            # Exclude per-number daily and hourly limits
            if num.calls_today >= num.daily_cap:
                continue
            if num.calls_this_hour >= num.hourly_cap:
                continue

            # Exclude verification mismatch
            if config.require_verified and not num.verified_for_provider:
                continue

            eligible.append(num)

        eligible_count = len(eligible)
        if eligible_count == 0:
            return CallerIdSelectionResult(
                success=False,
                provider=provider,
                source="none",
                reason=f"No eligible numbers found in did pool for provider {provider}.",
                warnings=warnings,
                candidate_count=candidate_count,
                eligible_count=0,
            )

        # Apply strategy sort
        selected = eligible[0]
        strategy = config.strategy.strip().lower()

        if strategy == "round_robin":
            # Round Robin: Choose the candidate used longest ago (last_used_at is None sorted first)
            def get_last_used(num: CallerIdNumber):
                if num.last_used_at is None:
                    return datetime.min.replace(tzinfo=timezone.utc)
                if isinstance(num.last_used_at, str):
                    return datetime.fromisoformat(num.last_used_at.replace("Z", "+00:00"))
                return num.last_used_at

            eligible.sort(key=get_last_used)
            selected = eligible[0]

        elif strategy == "least_used":
            # Least Used: Choose the candidate with the fewest calls today
            eligible.sort(key=lambda x: x.calls_today)
            selected = eligible[0]

        else:  # health_weighted (default)
            def calculate_health(num: CallerIdNumber) -> float:
                health = 100.0
                if num.spam_label_status == "suspected":
                    health -= 30.0
                elif num.spam_label_status == "flagged":
                    health -= 60.0
                elif num.spam_label_status == "blocked":
                    health -= 90.0
                
                health -= num.complaint_count * 10.0
                health -= num.dnc_count * 5.0
                
                if num.answer_rate is not None:
                    health += num.answer_rate * 50.0
                if num.transfer_rate is not None:
                    health += num.transfer_rate * 30.0
                
                return max(1.0, health)

            eligible.sort(key=calculate_health, reverse=True)
            selected = eligible[0]

        # Check if selection was cross-provider
        if selected.provider != provider:
            warnings.append(
                "Cross-provider caller ID may reduce attestation and increase call labeling risk."
            )

        return CallerIdSelectionResult(
            success=True,
            phone_number=selected.phone_number,
            provider=selected.provider,
            source=selected.source,
            reason="DID selected successfully via strategy: " + strategy,
            warnings=list(set(warnings)),
            candidate_count=candidate_count,
            eligible_count=eligible_count,
        )
