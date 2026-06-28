import asyncio
from storage.repository import Repository

async def test():
    repo = Repository()
    campaigns = await repo.store.query("outbound_campaigns", {})
    print("CAMPAIGNS:", campaigns)

asyncio.run(test())
