from datetime import date, datetime, timezone
from storage.repository import Repository

async def save_outcome_for_call(repository: Repository, call_id: str, campaign_id: str, outcome: str, cost: float = 0.0) -> None:
    """Trigger daily outcome recomputation for the campaign on the current date."""
    # Recompute daily outcome metrics rollup for today's date
    today = datetime.now(timezone.utc).date()
    await repository.recompute_daily_outcome_metric(campaign_id, today)
