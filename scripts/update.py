"""Weekly update: bring the index in line with current stars, Lists and awesome lists, then rebuild the site.

  python3 update.py            full run (needs ANTHROPIC_API_KEY [+ ANTHROPIC_WORKSPACE_ID])
  python3 update.py --dry-run  report what would change; no API calls, no writes to summaries

Steps: collect -> classify links -> fetch READMEs -> diff (new / changed / removed) -> refresh metadata ->
fetch + render new pages -> summarize with Claude Haiku (capped) -> licenses -> rebuild docs/ -> write report.
"""
import hashlib, json, os, subprocess, sys, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from common import *

DRY = "--dry-run" in sys.argv
MAX_REPOS, MAX_PAGES = 400, 600           # safety caps per run (cost stays well under $1)
RESUMMARIZE_AFTER_DAYS = 60               # a changed README is re-summarized only if the summary is this old
PAGE_RETRY_DAYS = 30                      # unreadable pages are retried after this long
PRICE_IN, PRICE_OUT = 0.10, 0.50          # Haiku standard (non-batch) $/MTok
PAGE_SUMS = ROOT / "summaries-pages"
HASHES = DATA / "readme_hashes.json"
ATTEMPTS = DATA / "page_attempts.json"
SCRIPTS = Path(__file__).resolve().parent
report = {"date": date.today().isoformat(), "dry_run": DRY}

def normalize_secret(name):
    """Tolerate common paste slips in CI secrets: surrounding quotes/whitespace, a 'NAME=' or 'KEY=' prefix."""
    v = os.environ.get(name, "").strip().strip('"').strip("'").strip()
    if "=" in v and v.split("=", 1)[0].strip().isupper():
        v = v.split("=", 1)[1].strip().strip('"').strip("'")
    if v: os.environ[name] = v
    return v

def fail(msg):
    report["error"] = msg
    (DATA / "last_update.json").write_text(json.dumps(report, indent=1))
    if os.environ.get("GITHUB_STEP_SUMMARY"):
        with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh: fh.write(f"### Stars weekly update FAILED\n\n{msg}\n")
    print("ERROR:", msg, flush=True); sys.exit(1)

def preflight():
    key, ws = normalize_secret("ANTHROPIC_API_KEY"), normalize_secret("ANTHROPIC_WORKSPACE_ID")
    shape = (f"key: prefix={key[:10]!r} length={len(key)}; "
             f"workspace: {'set, starts with wrkspc_' if ws.startswith('wrkspc_') else ('set but does not start with wrkspc_' if ws else 'not set')}")
    print("credentials:", shape, flush=True)
    if not key.startswith("sk-ant-"):
        fail(f"ANTHROPIC_API_KEY does not look like an Anthropic key ({shape}). Store only the sk-ant-... value.")
    import batch
    try:
        batch.client().messages.create(model=batch.MODEL, max_tokens=5, messages=[{"role": "user", "content": "Reply ok"}])
    except Exception as e:
        fail(f"Claude API check failed: {type(e).__name__}: {str(e)[:300]} ({shape})")

if not DRY:
    preflight()

def run(*args, env=None):
    print("$", " ".join(args), flush=True)
    subprocess.run([sys.executable, *args], cwd=SCRIPTS, check=True, env={**os.environ, **(env or {})})

def jload(p, default):
    return json.loads(p.read_text()) if p.exists() else default

def days_since(d):
    try: return (date.today() - datetime.strptime(d, "%Y-%m-%d").date()).days
    except Exception: return 10**6

def readme_sha(repo):
    p = README_DIR / f"{slug(repo)}.md"
    return hashlib.sha256(clean_markdown(p.read_text(errors="ignore")).encode()).hexdigest()[:16] if p.exists() else ""

def call_claude(jobs, label):
    """jobs: list of (key, params). Returns {key: (tool_input, usage)}; failures are left for next week."""
    import batch
    client = batch.client()
    out, cost_in, cost_out = {}, 0, 0
    def one(key, params):
        for attempt in range(3):
            try:
                m = client.messages.create(**params)
                tool = next((x for x in m.content if x.type == "tool_use"), None)
                return key, (dict(tool.input) if tool else None), m.usage
            except Exception as e:
                err = e; time.sleep(5 * (attempt + 1))
        print(f"  {label} failed: {key}: {err}", flush=True)
        report.setdefault("call_errors", [])
        if len(report["call_errors"]) < 3: report["call_errors"].append(f"{key}: {type(err).__name__}: {str(err)[:200]}")
        return key, None, None
    with ThreadPoolExecutor(4) as pool:
        for f in as_completed([pool.submit(one, k, p) for k, p in jobs]):
            key, data, usage = f.result()
            if data:
                out[key] = (data, usage); cost_in += usage.input_tokens; cost_out += usage.output_tokens
    report[f"{label}_tokens"] = {"input": cost_in, "output": cost_out}
    report[f"{label}_cost_usd"] = round(cost_in / 1e6 * PRICE_IN + cost_out / 1e6 * PRICE_OUT, 4)
    return out

def dedupe_keywords(out):
    seen = set()
    out["keywords"] = [k for k in (x.strip().lower() for x in out.get("keywords", [])) if k and not (k in seen or seen.add(k))]
    return out

# 1. collect current state -------------------------------------------------------------
run("collect.py")
run("classify_external.py")
for _ in range(6):  # fetch_readmes works in time-boxed passes
    run("fetch_readmes.py", "300")
    left = [it for it in read_jsonl(DATA / "items.jsonl")
            if not (README_DIR / f"{slug(it['repo'])}.md").exists() and not (README_DIR / f"{slug(it['repo'])}.missing").exists()]
    if not left: break

# 2. repos: diff ---------------------------------------------------------------------------
items = {slug(it["repo"]): it for it in read_jsonl(DATA / "items.jsonl")}
hashes = jload(HASHES, {})
existing = {p.stem: p for p in SUMS.glob("*.json")}
new = [s for s in items if s not in existing]
removed = [s for s in existing if s not in items]
changed, meta_updates = [], 0
for s, it in items.items():
    sha = readme_sha(it["repo"])
    if s in existing:
        rec = json.loads(existing[s].read_text())
        if s not in hashes:
            hashes[s] = sha                                  # first sight: baseline, no re-summary
        elif sha and sha != hashes[s] and days_since(rec.get("generated", "")) >= RESUMMARIZE_AFTER_DAYS:
            changed.append(s)
        # refresh cheap metadata (stars, lists, accounts, sources, ...) without calling the model
        upd = {k: v for k, v in it.items() if rec.get(k) != v}
        if upd and not DRY:
            rec.update(upd)
            if it.get("license"): rec["summary"]["license"] = it["license"]
            existing[s].write_text(json.dumps(rec, ensure_ascii=False, indent=1)); meta_updates += 1
        elif upd: meta_updates += 1
report.update({"repos_total": len(items), "repos_new": len(new), "repos_changed": len(changed),
               "repos_removed": len(removed), "repos_metadata_refreshed": meta_updates})

# 3. pages: diff + fetch -------------------------------------------------------------------
pages = {p["id"]: p for p in read_jsonl(DATA / "pages.jsonl")}
attempts = jload(ATTEMPTS, {})
page_sums = {p.stem: p for p in PAGE_SUMS.glob("*.json")} if PAGE_SUMS.exists() else {}
pages_removed = [i for i in page_sums if i not in pages]
# fetch a page if it has no summary and: never tried, OR it was readable (so its summary failed and must be retried),
# OR it was unreadable long enough ago to try again
to_fetch = [i for i, p in pages.items() if i not in page_sums and
            (i not in attempts or attempts[i].get("readable") or days_since(attempts[i].get("date", "")) >= PAGE_RETRY_DAYS)]
report.update({"pages_total": len(pages), "pages_to_fetch": len(to_fetch), "pages_removed": len(pages_removed)})
print(json.dumps(report, indent=1), flush=True)

if DRY:
    print("dry run: stopping before any fetch, model call or write"); sys.exit(0)

# guard: a partial fetch (e.g. one awesome list failing to download) must not wipe out part of the index
if len(removed) > 0.10 * max(len(existing), 1):
    print(f"WARNING: {len(removed)} repos would be removed (>10%); skipping removals this run"); report["repos_removed"] = 0; removed = []
if len(pages_removed) > 0.10 * max(len(page_sums), 1):
    print(f"WARNING: {len(pages_removed)} pages would be removed (>10%); skipping removals this run"); report["pages_removed"] = 0; pages_removed = []
for s in removed: existing[s].unlink()
for i in pages_removed: page_sums[i].unlink()

if to_fetch:
    idfile = CACHE / "only_ids.txt"; idfile.parent.mkdir(parents=True, exist_ok=True)
    idfile.write_text("\n".join(to_fetch[:MAX_PAGES * 2]))
    run("fetch_pages.py", env={"STARS_ONLY_IDS": str(idfile)})
    try:
        run("render_pages.py", env={"STARS_ONLY_IDS": str(idfile)})
    except Exception as e:
        print("render step skipped:", e)
    for i in to_fetch[:MAX_PAGES * 2]:
        p = CACHE / "pages" / f"{i}.json"
        if p.exists():
            r = json.loads(p.read_text())
            attempts[i] = {"date": report["date"], "readable": bool(r.get("readable")), "status": r.get("status"), "method": r.get("method")}

# 4. summarize ----------------------------------------------------------------------------
import batch, batch_pages
repo_jobs = [(s, batch.repo_params(items[s])) for s in (new + changed)[:MAX_REPOS]]
res = call_claude(repo_jobs, "repos") if repo_jobs else {}
for s, (out, usage) in res.items():
    it = items[s]; out = dedupe_keywords(out)
    if it.get("license"): out["license"] = it["license"]
    rec = {**it, "summary": out, "model": batch.MODEL, "readme_truncated": len(clean_markdown(
            (README_DIR / f"{s}.md").read_text(errors="ignore"))) > batch.README_CAP if (README_DIR / f"{s}.md").exists() else False,
           "usage": {"input": usage.input_tokens, "output": usage.output_tokens}, "generated": report["date"]}
    (SUMS / f"{s}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    hashes[s] = readme_sha(it["repo"])
page_jobs = []
for i in to_fetch:
    p = CACHE / "pages" / f"{i}.json"
    if p.exists():
        r = json.loads(p.read_text())
        if r.get("readable"): page_jobs.append((i, batch_pages.page_params(r, pages[i])))
page_jobs = page_jobs[:MAX_PAGES]
pres = call_claude(page_jobs, "pages") if page_jobs else {}
PAGE_SUMS.mkdir(exist_ok=True)
for i, (out, usage) in pres.items():
    r = json.loads((CACHE / "pages" / f"{i}.json").read_text())
    rec = {"id": i, "url": r["url"], "final_url": r.get("final_url"), "kind": r["kind"], "method": r.get("method"),
           "page_title": r.get("title", ""), "sources": pages[i].get("sources", []), "summary": dedupe_keywords(out),
           "model": batch.MODEL, "usage": {"input": usage.input_tokens, "output": usage.output_tokens}, "generated": report["date"]}
    (PAGE_SUMS / f"{i}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
report.update({"repos_summarized": len(res), "pages_summarized": len(pres)})
if (repo_jobs or page_jobs) and not res and not pres:
    fail(f"all {len(repo_jobs) + len(page_jobs)} Claude calls failed; first errors: {report.get('call_errors')}")

HASHES.write_text(json.dumps(hashes, indent=0, sort_keys=True))
ATTEMPTS.write_text(json.dumps(attempts, indent=0, sort_keys=True))

# 5. licenses + site --------------------------------------------------------------------------
run("fill_licenses.py")
run("build_site.py")

report["cost_usd"] = round(report.get("repos_cost_usd", 0) + report.get("pages_cost_usd", 0), 4)
line = (f"{report['date']}: +{report['repos_summarized']} repos ({report['repos_new']} new, {report['repos_changed']} changed), "
        f"-{report['repos_removed']} removed, +{report['pages_summarized']} pages, -{report['pages_removed']} pages removed, "
        f"{report['repos_metadata_refreshed']} metadata refreshes, ${report['cost_usd']:.2f}")
with open(DATA / "update_log.md", "a") as fh: fh.write(f"- {line}\n")
(DATA / "last_update.txt").write_text(line + "\n")
(DATA / "last_update.json").write_text(json.dumps(report, indent=1))
if os.environ.get("GITHUB_STEP_SUMMARY"):
    with open(os.environ["GITHUB_STEP_SUMMARY"], "a") as fh:
        fh.write(f"### Stars weekly update\n\n{line}\n\n```json\n{json.dumps(report, indent=1)}\n```\n")
print(line)
