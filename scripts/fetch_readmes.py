"""Download READMEs for every item in data/items.jsonl into cache/readme/. Resumable.

Usage: python3 fetch_readmes.py [time_budget_seconds]   (default 150; rerun until it reports 0 left)
"""
import asyncio, sys, time
import httpx
from common import *

BUDGET = float(sys.argv[1]) if len(sys.argv) > 1 else 150
CONC = 32
README_DIR.mkdir(parents=True, exist_ok=True)

def done(repo):
    s = slug(repo)
    return (README_DIR / f"{s}.md").exists() or (README_DIR / f"{s}.missing").exists()

async def fetch_one(client, repo):
    s = slug(repo)
    for name in README_NAMES:
        try:
            r = await client.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{name}")
        except httpx.HTTPError:
            return False  # transient: leave for next run
        if r.status_code == 200 and r.text.strip():
            (README_DIR / f"{s}.md").write_text(f"<!-- source: {name} -->\n" + r.text)
            return True
        if r.status_code not in (404, 400):
            return False  # rate limit / server error: retry next run
    (README_DIR / f"{s}.missing").write_text("")
    return True

async def main():
    t0 = time.time()
    todo = [it["repo"] for it in read_jsonl(DATA / "items.jsonl") if not done(it["repo"])]
    sem = asyncio.Semaphore(CONC)
    finished = 0
    async with httpx.AsyncClient(timeout=20, follow_redirects=True,
                                 headers={"User-Agent": "stars-index"}) as client:
        async def worker(repo):
            nonlocal finished
            if time.time() - t0 > BUDGET: return
            async with sem:
                if time.time() - t0 > BUDGET: return
                if await fetch_one(client, repo): finished += 1
        await asyncio.gather(*(worker(r) for r in todo))
    all_items = read_jsonl(DATA / "items.jsonl")
    left = sum(1 for it in all_items if not done(it["repo"]))
    found = len(list(README_DIR.glob("*.md"))); missing = len(list(README_DIR.glob("*.missing")))
    print(f"this run: {finished} done in {time.time()-t0:.0f}s | total readmes={found} missing={missing} left={left}")

asyncio.run(main())
