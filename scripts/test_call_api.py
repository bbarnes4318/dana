import asyncio
from storage.repository import Repository

async def test():
    repo = Repository()
    camps = await repo.store.query("outbound_campaigns", {})
    print("ALL CAMPAIGNS:", [(c.get("id"), c.get("name"), c.get("status")) for c in camps])
    
    active_camps = [c for c in camps if c.get("status") == "running"]
    print("ACTIVE CAMPAIGNS:", [(c.get("id"), c.get("name"), c.get("status")) for c in active_camps])

asyncio.run(test())
