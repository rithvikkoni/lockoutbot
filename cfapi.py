import aiohttp
import asyncio

async def fetch_submissions(handle):
    handle = handle.strip()
    url = f"https://codeforces.com/api/user.status?handle={handle}"
    print(f"🔍 Fetching submissions from: {url}")

    for attempt in range(2):  # try twice max
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        print(f"❌ Error (attempt {attempt+1}): Status {resp.status}")
                        await asyncio.sleep(1)  # short wait before retry
                        continue
                    data = await resp.json()
                    if data["status"] != "OK":
                        print("❌ Codeforces API returned error status.")
                        return None

                    # Process accepted submissions
                    solved = set()
                    for sub in data["result"]:
                        if sub["verdict"] == "OK":
                            pid = f"{sub['problem']['contestId']}-{sub['problem']['index']}"
                            solved.add(pid)
                    return solved

        except Exception as e:
            print(f"❌ Exception (attempt {attempt+1}) fetching {handle}:", e)
            await asyncio.sleep(1)

    return None  # All attempts failed

async def fetch_problemset():
    """Returns a list of problems from the Codeforces problemset."""
    async with aiohttp.ClientSession() as session:
        async with session.get("https://codeforces.com/api/problemset.problems") as resp:
            data = await resp.json()
    return data["result"]["problems"]