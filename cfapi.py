# cfapi.py
import aiohttp
import asyncio
import time

# Minimal global rate limiter: ensure at least MIN_INTERVAL seconds between any two CF API requests.
MIN_INTERVAL = 2.0  # seconds (Codeforces guideline ~1 call per 2 seconds)

_rate_lock = asyncio.Lock()
_last_call = 0.0

async def _wait_rate_slot():
    """Ensure spacing of MIN_INTERVAL between CF API calls."""
    global _last_call
    async with _rate_lock:
        now = time.time()
        wait = MIN_INTERVAL - (now - _last_call)
        if wait > 0:
            await asyncio.sleep(wait)
        _last_call = time.time()

async def fetch_submissions(handle: str):
    """
    Returns a dict mapping problem pid ("contestId-index") -> earliest accepted submission time (creationTimeSeconds)
    or None on error.
    """
    handle = handle.strip()
    url = f"https://codeforces.com/api/user.status?handle={handle}"
    for attempt in range(2):
        try:
            await _wait_rate_slot()
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(1)
                        continue
                    data = await resp.json()
            if data.get("status") != "OK":
                return None
            solved = {}
            for sub in data.get("result", []):
                if sub.get("verdict") != "OK":
                    continue
                prob = sub.get("problem", {})
                pid = f"{prob.get('contestId')}-{prob.get('index')}"
                t = sub.get("creationTimeSeconds", 0)
                # keep earliest accepted time (first AC)
                if pid not in solved or (t and t < solved[pid]):
                    solved[pid] = t
            return solved
        except Exception:
            await asyncio.sleep(1)
    return None

async def fetch_problemset():
    """
    Returns the list of problems (problem dicts) or None on error.
    """
    url = "https://codeforces.com/api/problemset.problems"
    for attempt in range(2):
        try:
            await _wait_rate_slot()
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        await asyncio.sleep(1)
                        continue
                    data = await resp.json()
            if data.get("status") != "OK":
                return None
            return data.get("result", {}).get("problems", [])
        except Exception:
            await asyncio.sleep(1)
    return None
