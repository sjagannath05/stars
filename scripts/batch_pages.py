"""Summarize fetched web pages (cache/pages/*.json) with Claude Haiku via the Message Batches API.

Same split workflow as batch.py, with its own state file (data/batches_pages.json):
  build [--limit N]  ->  submit-files <files>  ->  status  ->  download  ->  apply
Writes summaries-pages/<page id>.json.
"""
import json, sys, time
import batch
from common import *

batch.STATE = DATA / "batches_pages.json"
PAGES_DIR = CACHE / "pages"
OUT = ROOT / "summaries-pages"
TEXT_CAP = 24000

TOOL_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["name", "one_liner", "category", "keywords", "use_cases", "what_it_does", "pricing", "platforms", "notable", "caveats"],
    "properties": {
        "name": {"type": "string", "description": "Product or project name."},
        "one_liner": {"type": "string", "description": "One sentence: what it is."},
        "category": {"type": "string", "description": "2-4 word category, e.g. 'macos clipboard manager', 'ai image generator'."},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "20-35 distinct lowercase search terms incl. plain-English phrases a person would type."},
        "use_cases": {"type": "array", "items": {"type": "string"}, "description": "4-7 short phrases: real tasks someone would use it for."},
        "what_it_does": {"type": "string", "description": "3-5 sentences of concrete capabilities, no marketing."},
        "pricing": {"type": "string", "description": "One of: open source, free, freemium, paid, unknown — only from the page."},
        "platforms": {"type": "array", "items": {"type": "string"}, "description": "Platforms named on the page (macOS, Windows, Linux, iOS, web, Docker, ...)."},
        "notable": {"type": "array", "items": {"type": "string"}, "description": "2-4 distinctive features."},
        "caveats": {"type": "array", "items": {"type": "string"}, "description": "0-3 limitations stated or clearly implied (paid tiers, platform limits, discontinued)."},
    }}
ARTICLE_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["title", "one_liner", "category", "keywords", "takeaways", "summary", "format", "level", "technologies"],
    "properties": {
        "title": {"type": "string", "description": "Title of the article, tutorial, book, talk or paper."},
        "one_liner": {"type": "string", "description": "One sentence: what you learn from it."},
        "category": {"type": "string", "description": "2-4 word topic, e.g. 'distributed systems', 'engineering management', 'build a compiler'."},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "15-30 distinct lowercase search terms, incl. plain-English questions it answers."},
        "takeaways": {"type": "array", "items": {"type": "string"}, "description": "3-5 key points or what you will build/learn."},
        "summary": {"type": "string", "description": "3-5 sentences on what it covers and how."},
        "format": {"type": "string", "description": "One of: article, tutorial, book, course, talk, paper, reference, list, other."},
        "level": {"type": "string", "description": "One of: beginner, intermediate, advanced, mixed, unknown."},
        "technologies": {"type": "array", "items": {"type": "string"}, "description": "Languages/technologies used or discussed."},
    }}
SYSTEM = """You write short summaries of web pages for a personal search index of tools and reading material.
Rules:
- Use only facts from the page text and the listing given. Never invent features, prices, platforms or claims.
- Ignore navigation, cookie banners, sign-up prompts, testimonials and ads.
- If the page text is mostly marketing with few facts, keep fields short rather than guessing; say "unknown" where asked.
- keywords: technical terms, synonyms, alternatives it is compared with (only if named), and plain-English phrases a person would type.
  Do not start more than 4 keywords with the same word."""

def build(args):
    st = batch.load_state()
    st.setdefault("id_map", {}); st.setdefault("batches", [])
    queued = set(st["id_map"].values())
    meta = {r["id"]: r for r in read_jsonl(DATA / "pages.jsonl")}
    todo = []
    for p in sorted(PAGES_DIR.glob("*.json")):
        rec = json.loads(p.read_text())
        if rec.get("readable") and not (OUT / f"{rec['id']}.json").exists() and rec["id"] not in queued:
            todo.append((rec, meta.get(rec["id"], {})))
    if "--limit" in args:
        todo = todo[:int(args[args.index("--limit") + 1])]
    out_dir = DATA / "requests"; out_dir.mkdir(exist_ok=True)
    tag = "pages-" + time.strftime("%Y%m%d-%H%M%S"); files = []
    for i in range(0, len(todo), batch.CHUNK):
        rows = []
        for rec, m in todo[i:i + batch.CHUNK]:
            kind = rec["kind"]
            schema = ARTICLE_SCHEMA if kind == "article" else TOOL_SCHEMA
            lines = [f"URL: {rec['url']}"]
            if rec.get("title"): lines.append(f"Page title: {rec['title']}")
            if rec.get("description"): lines.append(f"Meta description: {rec['description']}")
            for s in (m.get("sources") or [])[:2]:
                lines.append(f"Listed in {s['list']} under '{s['section']}': {s['entry']}")
            if rec.get("method") == "wayback": lines.append("(Text comes from an archived copy of the page.)")
            what = "article / tutorial / reading material" if kind == "article" else "tool / product / project page"
            user = "\n".join(lines) + f"\n\nThis is a {what}.\n\nPAGE TEXT:\n\"\"\"\n{rec['text'][:TEXT_CAP]}\n\"\"\"\n\nCall save_summary with the summary."
            cid = "p" + rec["id"]
            st["id_map"][cid] = rec["id"]
            rows.append({"custom_id": cid, "params": {
                "model": batch.MODEL, "max_tokens": 2500, "system": SYSTEM,
                "tools": [{"name": "save_summary", "description": "Save the page summary. Fill every field.", "input_schema": schema, "strict": True}],
                "tool_choice": {"type": "tool", "name": "save_summary"},
                "messages": [{"role": "user", "content": user}]}})
        f = out_dir / f"{tag}-{i // batch.CHUNK:02d}.jsonl"
        write_jsonl(f, rows); files.append(f.name)
    batch.save_state(st)
    print(f"built {len(todo)} page requests in {len(files)} file(s): {files}")

def apply(args):
    st = batch.load_state()
    meta = {r["id"]: r for r in read_jsonl(DATA / "pages.jsonl")}
    OUT.mkdir(exist_ok=True)
    tin = tout = ok = bad = 0; failures = []
    order = [DATA / "results" / f"{b['id']}.jsonl" for b in st["batches"]]
    for f in [x for x in order if x.exists()]:
        for res in read_jsonl(f):
            pid = st["id_map"].get(res["custom_id"]); r = res["result"]
            if r["type"] != "succeeded":
                bad += 1; failures.append(f"{pid}: {r['type']}"); continue
            m = r["message"]; tool = next((x for x in m["content"] if x["type"] == "tool_use"), None)
            if not tool:
                bad += 1; failures.append(f"{pid}: no tool output"); continue
            out = dict(tool["input"]); seen = set()
            out["keywords"] = [k for k in (x.strip().lower() for x in out.get("keywords", [])) if k and not (k in seen or seen.add(k))]
            page = json.loads((PAGES_DIR / f"{pid}.json").read_text())
            rec = {"id": pid, "url": page["url"], "final_url": page.get("final_url"), "kind": page["kind"],
                   "method": page.get("method"), "page_title": page.get("title", ""),
                   "sources": meta.get(pid, {}).get("sources", []), "summary": out, "model": batch.MODEL,
                   "usage": {"input": m["usage"]["input_tokens"], "output": m["usage"]["output_tokens"]},
                   "generated": time.strftime("%Y-%m-%d")}
            (OUT / f"{pid}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
            tin += m["usage"]["input_tokens"]; tout += m["usage"]["output_tokens"]; ok += 1
    print(f"applied ok={ok} failed={bad} input_tokens={tin} output_tokens={tout} est_cost=${tin/1e6*batch.PRICE_IN + tout/1e6*batch.PRICE_OUT:.2f}")
    if failures: (DATA / "failures_pages.txt").write_text("\n".join(failures))

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"build": build, "apply": apply, "submit-files": batch.submit_files, "status": batch.status,
     "download": batch.download}[cmd](sys.argv[2:])
