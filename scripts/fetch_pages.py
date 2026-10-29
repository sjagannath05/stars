"""Fetch every URL in data/pages.jsonl and keep the readable main text in cache/pages/<id>.json.

Run on a machine with normal internet access (your Mac):  bash scripts/run_fetch_pages.sh
Resumable: pages already in cache/pages/ are skipped. Safe to stop and rerun.
"""
import io, json, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse
import httpx
import trafilatura
from common import *

OUT = CACHE / "pages"
OUT.mkdir(parents=True, exist_ok=True)
WORKERS, PER_HOST = 16, 2
MAX_BYTES, MAX_TEXT, THIN = 6_000_000, 40_000, 400
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 14_0) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
host_locks, host_guard = {}, threading.Lock()

def host_sem(url):
    h = urlparse(url).netloc.lower()
    with host_guard:
        return host_locks.setdefault(h, threading.Semaphore(PER_HOST))

def extract_pdf(data):
    try:
        from pypdf import PdfReader
        r = PdfReader(io.BytesIO(data))
        title = (r.metadata.title if r.metadata else "") or ""
        return title, "\n".join((p.extract_text() or "") for p in r.pages[:20])
    except Exception:
        return "", ""

def extract_html(html, url):
    text = trafilatura.extract(html, url=url, include_comments=False, include_tables=True, favor_recall=True) or ""
    meta = trafilatura.extract_metadata(html, default_url=url)
    return ((meta.title if meta else "") or ""), ((meta.description if meta else "") or ""), text

def get(client, url):
    with host_sem(url):
        r = client.get(url)
    return r, r.headers.get("content-type", "").lower(), r.content[:MAX_BYTES]

def fetch_one(client, row):
    url = row["url"]
    rec = {"id": row["id"], "url": url, "kind": row["kind"], "fetched": time.strftime("%Y-%m-%d"),
           "status": None, "final_url": None, "method": "direct", "title": "", "description": "", "text": ""}
    try:
        r, ctype, data = get(client, url)
        rec["status"], rec["final_url"] = r.status_code, str(r.url)
        if r.status_code < 400:
            if "pdf" in ctype or url.lower().endswith(".pdf"):
                rec["method"] = "pdf"; rec["title"], rec["text"] = extract_pdf(data)
            else:
                rec["title"], rec["description"], rec["text"] = extract_html(data.decode(r.encoding or "utf-8", "ignore"), str(r.url))
    except Exception as e:
        rec["status"] = "error: " + type(e).__name__
    if len(rec["text"]) < THIN:  # dead or unreadable: try a Wayback Machine snapshot
        try:
            w = client.get("https://archive.org/wayback/available", params={"url": url}).json()
            snap = (w.get("archived_snapshots") or {}).get("closest") or {}
            if snap.get("available") and snap.get("url"):
                r2, ctype2, data2 = get(client, snap["url"])
                if r2.status_code < 400:
                    if "pdf" in ctype2:
                        (t, x), d = extract_pdf(data2), ""
                    else:
                        t, d, x = extract_html(data2.decode(r2.encoding or "utf-8", "ignore"), url)
                    if len(x) > len(rec["text"]):
                        rec.update({"method": "wayback", "wayback_url": snap["url"],
                                    "title": rec["title"] or t, "description": rec["description"] or d, "text": x})
        except Exception:
            pass
    rec["text"] = rec["text"][:MAX_TEXT]
    rec["readable"] = len(rec["text"]) >= THIN
    (OUT / f"{row['id']}.json").write_text(json.dumps(rec, ensure_ascii=False))
    return rec

def main():
    rows = read_jsonl(DATA / "pages.jsonl")
    if len(sys.argv) > 1 and sys.argv[1].isdigit():
        rows = rows[:int(sys.argv[1])]
    todo = [r for r in rows if not (OUT / f"{r['id']}.json").exists()]
    print(f"{time.strftime('%H:%M')}  {len(rows)} pages, {len(rows) - len(todo)} already fetched, {len(todo)} to go", flush=True)
    t0, done, readable, wayback = time.time(), 0, 0, 0
    limits = httpx.Limits(max_connections=WORKERS * 2, max_keepalive_connections=WORKERS)
    with httpx.Client(headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=20,
                      follow_redirects=True, limits=limits) as client, ThreadPoolExecutor(WORKERS) as pool:
        futs = [pool.submit(fetch_one, client, r) for r in todo]
        for f in as_completed(futs):
            try:
                rec = f.result()
            except Exception as e:
                print("  worker error:", e, flush=True); continue
            done += 1; readable += rec["readable"]; wayback += rec["method"] == "wayback"
            if done % 100 == 0 or done == len(todo):
                rate = done / (time.time() - t0)
                print(f"{time.strftime('%H:%M')}  {done}/{len(todo)}  readable={readable}  via_wayback={wayback}  "
                      f"{rate:.1f}/s  eta {int((len(todo) - done) / max(rate, 0.01) / 60)} min", flush=True)
    allrec = [json.loads(p.read_text()) for p in OUT.glob("*.json")]
    ok = sum(1 for x in allrec if x.get("readable"))
    print(f"{time.strftime('%H:%M')}  DONE: {len(allrec)} fetched, {ok} readable, {len(allrec) - ok} thin/unreachable "
          f"({time.time() - t0:.0f}s this run)", flush=True)

if __name__ == "__main__":
    main()
