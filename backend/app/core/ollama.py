import aiohttp


async def model_digests(client: aiohttp.ClientSession, base_url: str) -> dict[str, str]:
    async with client.get(f"{base_url.rstrip('/')}/api/tags", allow_redirects=False) as response:
        response.raise_for_status()
        data = await response.json()
    return {record["name"]: record["digest"] for record in data["models"]}
