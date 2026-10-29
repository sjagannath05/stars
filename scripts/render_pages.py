"""Second pass for pages the plain fetch couldn't read: render them in a real headless browser.

Targets cache/pages/*.json that are not readable and either loaded but were empty (JavaScript-only sites),
were blocked (403/429/503) or timed out. Dead domains and 404s are skipped.
Run on the Mac:  bash scripts/run_render_pages.sh      (resumable; marks attempts with "rendered": true)
"""
import asyncio, json, sys, time
import trafilatura
from playwright.async_api import async_playwright
from common import *

PAGES = CACHE / "pages"
CONC, THIN, MAX_TEXT = 4, 400, 40000
import os
from pathlib import Path
ONLY = set(Path(os.environ["STARS_ONLY_IDS"]).read_text().split()) if os.environ.get("STARS_ONLY_IDS") else None
RETRY_STATUS = {200, 202, 403, 406, 429, 503, 520, 521, 522, 523}

def wants(rec):
    if rec.get("readable") or rec.get("rendered"): return False
    st = rec.get("status")
    return st in RETRY_STATUS or (isinstance(st, str) and ("Timeout" in st or "RemoteProtocol" in st or "ReadError" in st))

async def render(ctx, path, rec, sem, stats):
    async with sem:
        page = await ctx.new_page()
        try:
            await page.goto(rec["url"], wait_until="domcontentloaded", timeout=25000)
            try: await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception: pass
            await page.wait_for_timeout(1200)
            html = await page.content()
            text = trafilatura.extract(html, url=page.url, include_comments=False, include_tables=True, favor_recall=True) or ""
            if len(text) < THIN:
                text = await page.evaluate("() => (document.querySelector('main') || document.body).innerText") or ""
            meta = trafilatura.extract_metadata(html, default_url=page.url)
            if len(text.strip()) >= THIN:
                rec.update({"method": "browser", "final_url": page.url, "text": text.strip()[:MAX_TEXT], "readable": True,
                            "title": rec.get("title") or (meta.title if meta else "") or await page.title(),
                            "description": rec.get("description") or ((meta.description if meta else "") or "")})
                stats["recovered"] += 1
            else:
                stats["still_thin"] += 1
        except Exception as e:
            rec["render_error"] = type(e).__name__; stats["error"] += 1
        finally:
            rec["rendered"] = True
            path.write_text(json.dumps(rec, ensure_ascii=False))
            await page.close()
            stats["done"] += 1
            if stats["done"] % 50 == 0:
                print(f"{time.strftime('%H:%M')}  {stats['done']}/{stats['total']}  recovered={stats['recovered']}  "
                      f"still_thin={stats['still_thin']}  errors={stats['error']}", flush=True)

async def main():
    todo = []
    for p in PAGES.glob("*.json"):
        rec = json.loads(p.read_text())
        if wants(rec) and (ONLY is None or rec["id"] in ONLY): todo.append((p, rec))
    if len(sys.argv) > 1 and sys.argv[1].isdigit(): todo = todo[:int(sys.argv[1])]
    stats = {"done": 0, "total": len(todo), "recovered": 0, "still_thin": 0, "error": 0}
    print(f"{time.strftime('%H:%M')}  {len(todo)} pages to render", flush=True)
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        ctx = await browser.new_context(locale="en-US", viewport={"width": 1280, "height": 900},
                                        user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
        await ctx.route("**/*", lambda route: route.abort() if route.request.resource_type in ("image", "media", "font") else route.continue_())
        sem = asyncio.Semaphore(CONC)
        await asyncio.gather(*(render(ctx, p, r, sem, stats) for p, r in todo))
        await browser.close()
    print(f"{time.strftime('%H:%M')}  DONE: rendered {stats['done']}, recovered {stats['recovered']}, "
          f"still thin {stats['still_thin']}, errors {stats['error']}", flush=True)

asyncio.run(main())
