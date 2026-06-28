from dotenv import load_dotenv
load_dotenv()

import asyncio
from storage.repository import Repository

async def test():
    repo = Repository()
    camps = await repo.store.query("outbound_campaigns", {})
    print("REAL CAMPAIGNS:", [(c.get("id"), c.get("name"), c.get("status")) for c in camps])

asyncio.run(test())
