"""Summarize items with Claude Haiku via the Message Batches API.

  python3 batch.py submit [--limit N] [--only-missing]   build + submit batches (2000 requests each)
  python3 batch.py status                                 show batch progress
  python3 batch.py collect                                write finished results to summaries/*.json
Split mode (when the machine holding the files can't reach the API):
  build [--limit N]  ->  submit-files <files>  ->  status  ->  download  ->  apply
Needs ANTHROPIC_API_KEY in ../.env. State lives in data/batches.json.
"""
import json, sys, time
from pathlib import Path
import anthropic
from common import *

MODEL = "claude-haiku-5-5"
PRICE_IN, PRICE_OUT = 0.05, 0.25          # batch $/MTok, prompts <= 100k tokens
README_CAP = 40000                         # chars of cleaned README sent
CHUNK = 2000
STATE = DATA / "batches.json"

SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["one_liner", "category", "keywords", "use_cases", "what_it_does", "how_to_run", "stack",
                 "notable", "caveats", "license"],
    "properties": {
        "one_liner": {"type": "string", "description": "One sentence: what it is."},
        "category": {"type": "string", "description": "2-4 word category, e.g. 'web scraping', 'self-hosted file sharing', 'kubernetes tooling'."},
        "keywords": {"type": "array", "items": {"type": "string"}, "description": "25-40 distinct lowercase search terms."},
        "what_it_does": {"type": "string", "description": "4-6 sentences of concrete capabilities, no marketing."},
        "use_cases": {"type": "array", "items": {"type": "string"}, "description": "5-8 short phrases: real tasks someone would use it for."},
        "how_to_run": {"type": "array", "items": {"type": "string"}, "description": "Up to 6 install/run commands or steps from the README; empty if none."},
        "stack": {"type": "array", "items": {"type": "string"}, "description": "Languages, runtimes, major dependencies named in the README."},
        "notable": {"type": "array", "items": {"type": "string"}, "description": "3-5 distinctive features."},
        "caveats": {"type": "array", "items": {"type": "string"}, "description": "1-3 limitations stated or clearly implied (platform limits, heavy setup, unmaintained, paid tiers)."},
        "license": {"type": "string", "description": "License named in the README, else 'unknown'."},
    },
}

SYSTEM = """You write one-page summaries of GitHub repositories for a personal search index.
Rules:
- Use only facts from the README and the metadata given. Never invent features, platforms, dependencies or integrations.
- Ignore sponsor, advertising, badge, star-history and contributor sections.
- If the README is missing or nearly empty, say so plainly, rely on the metadata, and keep every field short rather than guessing.
- keywords: mix technical terms, synonyms, tools it is an alternative to (only if named or obvious from the README),
  and plain-English phrases a person would type when looking for this (e.g. "share files with my team").
  Do not start more than 4 keywords with the same word. Do not mention products or sites the README does not mention.
- If the repository is itself a list of links (an "awesome" list or tutorial collection), describe what the list covers and how it is organized."""

def build_user(it):
    s = slug(it["repo"])
    p = README_DIR / f"{s}.md"
    readme = clean_markdown(p.read_text(errors="ignore")) if p.exists() else ""
    truncated = len(readme) > README_CAP
    readme = readme[:README_CAP]
    meta = [f"Repository: {it['repo']}"]
    if it.get("description"): meta.append(f"GitHub description: {it['description']}")
    if it.get("topics"): meta.append("GitHub topics: " + ", ".join(it["topics"]))
    if it.get("language"): meta.append(f"Main language: {it['language']}")
    if it.get("license"): meta.append(f"License (from GitHub): {it['license']}")
    for src in it.get("sources", [])[:3]:
        meta.append(f"Listed in {src['list']} under '{src['section']}': {src['entry']}")
    body = readme if readme else "(README not available)"
    note = "\n(README truncated for length.)" if truncated else ""
    return "\n".join(meta) + f"\n\nREADME:\n\"\"\"\n{body}\n\"\"\"{note}\n\nCall save_summary with the summary.", truncated

def repo_params(it):
    user, _ = build_user(it)
    return {"model": MODEL, "max_tokens": 3000, "system": SYSTEM,
            "tools": [{"name": "save_summary", "description": "Save the repository summary. Fill every field.", "input_schema": SCHEMA, "strict": True}],
            "tool_choice": {"type": "tool", "name": "save_summary"},
            "messages": [{"role": "user", "content": user}]}

def load_state():
    return json.loads(STATE.read_text()) if STATE.exists() else {"batches": [], "id_map": {}}

def save_state(st):
    STATE.write_text(json.dumps(st, indent=1))

def client():
    load_env()
    if not os.environ.get("ANTHROPIC_API_KEY"):
        sys.exit("ANTHROPIC_API_KEY missing: put it in ~/repos/stars/.env")
    hdr = {"anthropic-workspace-id": os.environ["ANTHROPIC_WORKSPACE_ID"]} if os.environ.get("ANTHROPIC_WORKSPACE_ID") else None
    return anthropic.Anthropic(default_headers=hdr)

def build(args):
    """Write batch request files (no API calls). Records custom_id -> item slug in state."""
    st = load_state()
    items = read_jsonl(DATA / "items.jsonl")
    queued = set(st["id_map"].values())
    def incomplete(it):
        p = SUMS / f"{slug(it['repo'])}.json"
        if not p.exists(): return False
        sm = json.loads(p.read_text()).get("summary", {})
        return not (sm.get("keywords") and sm.get("category") and sm.get("use_cases"))
    if "--redo-incomplete" in args:
        todo = [it for it in items if incomplete(it)]
    else:
        todo = [it for it in items if not (SUMS / f"{slug(it['repo'])}.json").exists() and slug(it["repo"]) not in queued]
    if "--limit" in args:
        todo = todo[:int(args[args.index("--limit") + 1])]
    out_dir = DATA / "requests"; out_dir.mkdir(exist_ok=True)
    tag = time.strftime("%Y%m%d-%H%M%S")
    files = []
    for i in range(0, len(todo), CHUNK):
        rows = []
        for it in todo[i:i + CHUNK]:
            cid = f"r{len(st['id_map']) + 1:06d}"
            st["id_map"][cid] = slug(it["repo"])
            user, _ = build_user(it)
            rows.append({"custom_id": cid, "params": {
                "model": MODEL, "max_tokens": 3000, "system": SYSTEM,
                "tools": [{"name": "save_summary", "description": "Save the repository summary. Fill every field.", "input_schema": SCHEMA, "strict": True}],
                "tool_choice": {"type": "tool", "name": "save_summary"},
                "messages": [{"role": "user", "content": user}]}})
        f = out_dir / f"{tag}-{i // CHUNK:02d}.jsonl"
        write_jsonl(f, rows); files.append(f.name)
    save_state(st)
    print(f"built {len(todo)} requests in {len(files)} file(s): {files}")

def submit_files(args):
    """Submit request files (paths in args) as batches. Run where the API is reachable."""
    st = load_state(); c = client()
    for path in args:
        rows = read_jsonl(path)
        b = c.messages.batches.create(requests=rows)
        st["batches"].append({"id": b.id, "file": Path(path).name, "count": len(rows),
                              "status": b.processing_status, "downloaded": False, "collected": False})
        save_state(st); print(f"submitted {b.id} ({len(rows)} requests) from {Path(path).name}")

def download(args):
    """Save raw results of ended batches to data/results/<batch_id>.jsonl."""
    st = load_state(); c = client()
    out = DATA / "results"; out.mkdir(exist_ok=True)
    for b in st["batches"]:
        if b.get("downloaded"): continue
        r = c.messages.batches.retrieve(b["id"])
        if r.processing_status != "ended":
            print(f"{b['id']} still {r.processing_status}"); continue
        with open(out / f"{b['id']}.jsonl", "w") as fh:
            for res in c.messages.batches.results(b["id"]):
                fh.write(res.model_dump_json() + "\n")
        b["downloaded"] = True; save_state(st); print(f"downloaded {b['id']}")

def apply(args):
    """Turn data/results/*.jsonl into summaries/*.json (no API needed)."""
    st = load_state()
    items = {slug(it["repo"]): it for it in read_jsonl(DATA / "items.jsonl")}
    SUMS.mkdir(exist_ok=True)
    tot_in = tot_out = ok = bad = 0; failures = []
    order = [DATA / "results" / f"{b['id']}.jsonl" for b in st["batches"]]
    for f in [x for x in order if x.exists()]:  # submission order: later batches override earlier ones
        for res in read_jsonl(f):
            s = st["id_map"].get(res["custom_id"]); it = items.get(s, {"repo": s})
            r = res["result"]
            if r["type"] != "succeeded":
                bad += 1; failures.append(f"{it['repo']}: {r['type']}"); continue
            m = r["message"]
            tool = next((x for x in m["content"] if x["type"] == "tool_use"), None)
            if not tool:
                bad += 1; failures.append(f"{it['repo']}: no tool output"); continue
            out = dict(tool["input"]); seen = set()
            out["keywords"] = [k for k in (x.strip().lower() for x in out.get("keywords", [])) if k and not (k in seen or seen.add(k))]
            if it.get("license"): out["license"] = it["license"]
            _, truncated = build_user(it) if "repo" in it and s in items else ("", False)
            rec = {**it, "summary": out, "model": MODEL, "readme_truncated": truncated,
                   "usage": {"input": m["usage"]["input_tokens"], "output": m["usage"]["output_tokens"]},
                   "generated": time.strftime("%Y-%m-%d")}
            (SUMS / f"{s}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
            tot_in += m["usage"]["input_tokens"]; tot_out += m["usage"]["output_tokens"]; ok += 1
    cost = tot_in / 1e6 * PRICE_IN + tot_out / 1e6 * PRICE_OUT
    print(f"applied ok={ok} failed={bad} input_tokens={tot_in} output_tokens={tot_out} est_cost=${cost:.2f}")
    if failures:
        (DATA / "failures.txt").write_text("\n".join(failures)); print("failures -> data/failures.txt")

def submit(args):
    st = load_state()
    items = read_jsonl(DATA / "items.jsonl")
    queued = {cid_item for b in st["batches"] if b.get("status") != "failed" for cid_item in b["ids"]}
    todo = [it for it in items if not (SUMS / f"{slug(it['repo'])}.json").exists()
            and slug(it["repo"]) not in queued]
    if "--limit" in args:
        todo = todo[:int(args[args.index("--limit") + 1])]
    if not todo:
        print("nothing to submit"); return
    c = client()
    for i in range(0, len(todo), CHUNK):
        chunk = todo[i:i + CHUNK]
        reqs, ids = [], []
        for it in chunk:
            n = len(st["id_map"]) + 1
            cid = f"r{n:06d}"
            st["id_map"][cid] = slug(it["repo"])
            user, _ = build_user(it)
            reqs.append({"custom_id": cid, "params": {
                "model": MODEL, "max_tokens": 3000, "system": SYSTEM,
                "tools": [{"name": "save_summary", "description": "Save the repository summary. Fill every field.", "input_schema": SCHEMA, "strict": True}],
                "tool_choice": {"type": "tool", "name": "save_summary"},
                "messages": [{"role": "user", "content": user}]}})
            ids.append(slug(it["repo"]))
        b = c.messages.batches.create(requests=reqs)
        st["batches"].append({"id": b.id, "created": b.created_at.isoformat(), "count": len(reqs),
                              "ids": ids, "status": b.processing_status, "collected": False})
        save_state(st)
        print(f"submitted {b.id} with {len(reqs)} requests")

def status(args):
    st = load_state(); c = client()
    for b in st["batches"]:
        r = c.messages.batches.retrieve(b["id"])
        b["status"] = r.processing_status
        rc = r.request_counts
        print(f"{b['id']} {r.processing_status:12} ok={rc.succeeded} err={rc.errored} "
              f"processing={rc.processing} expired={rc.expired} collected={b['collected']}")
    save_state(st)

def collect(args):
    st = load_state(); c = client()
    items = {slug(it["repo"]): it for it in read_jsonl(DATA / "items.jsonl")}
    SUMS.mkdir(exist_ok=True)
    tot_in = tot_out = ok = bad = 0
    failures = []
    for b in st["batches"]:
        if b["collected"]: continue
        r = c.messages.batches.retrieve(b["id"])
        if r.processing_status != "ended":
            print(f"{b['id']} still {r.processing_status}"); continue
        for res in c.messages.batches.results(b["id"]):
            s = st["id_map"][res.custom_id]
            it = items.get(s, {"repo": s})
            if res.result.type != "succeeded":
                bad += 1; failures.append(f"{it['repo']}: {res.result.type}"); continue
            m = res.result.message
            tool = next((x for x in m.content if x.type == "tool_use"), None)
            if not tool:
                bad += 1; failures.append(f"{it['repo']}: no tool output"); continue
            out = dict(tool.input)
            seen = set()
            out["keywords"] = [k for k in (x.strip().lower() for x in out.get("keywords", [])) if k and not (k in seen or seen.add(k))]
            if it.get("license"): out["license"] = it["license"]
            _, truncated = build_user(it)
            rec = {**{k: v for k, v in it.items()}, "summary": out, "model": MODEL,
                   "readme_truncated": truncated,
                   "usage": {"input": m.usage.input_tokens, "output": m.usage.output_tokens},
                   "generated": time.strftime("%Y-%m-%d")}
            (SUMS / f"{s}.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
            tot_in += m.usage.input_tokens; tot_out += m.usage.output_tokens; ok += 1
        b["collected"] = True
        save_state(st)
    cost = tot_in / 1e6 * PRICE_IN + tot_out / 1e6 * PRICE_OUT
    print(f"collected ok={ok} failed={bad} input_tokens={tot_in} output_tokens={tot_out} est_cost=${cost:.2f}")
    if failures:
        (DATA / "failures.txt").write_text("\n".join(failures)); print("failures -> data/failures.txt")

if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    {"build": build, "submit-files": submit_files, "download": download, "apply": apply,
     "submit": submit, "status": status, "collect": collect}[cmd](sys.argv[2:])
