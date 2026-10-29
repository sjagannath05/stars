"""Collect both accounts' stars plus every GitHub repo / external link in the awesome lists.

Writes data/items.jsonl (GitHub repos to summarize) and data/external.jsonl (non-GitHub links, later phase).
"""
import json, os, re, sys
import httpx
from common import *

load_env()
H = {"Accept": "application/vnd.github+json", "User-Agent": "stars-index"}
if os.environ.get("GITHUB_TOKEN"):
    H["Authorization"] = "Bearer " + os.environ["GITHUB_TOKEN"]
c = httpx.Client(headers=H, timeout=30, follow_redirects=True)

# --- stars ---------------------------------------------------------------
lists = {}
lp = DATA / "lists.txt"
if lp.exists():
    for line in lp.read_text().splitlines():
        if line.count("|") < 2: continue
        acct, name, repos = line.split("|", 2)
        for r in repos.split():
            lists.setdefault(r.lower(), set()).add(name)

items = {}
for acct in ACCOUNTS:
    page = 1
    while True:
        r = c.get(f"https://api.github.com/users/{acct}/starred", params={"per_page": 100, "page": page})
        r.raise_for_status()
        rows = r.json()
        for x in rows:
            key = x["full_name"].lower()
            it = items.setdefault(key, {
                "id": key, "repo": x["full_name"], "kind": "star",
                "description": x.get("description") or "", "topics": x.get("topics") or [],
                "language": x.get("language") or "", "stars": x.get("stargazers_count", 0),
                "license": (x.get("license") or {}).get("spdx_id") or "",
                "homepage": x.get("homepage") or "", "pushed_at": (x.get("pushed_at") or "")[:10],
                "archived": x.get("archived", False), "accounts": [], "lists": [], "sources": []})
            if acct not in it["accounts"]: it["accounts"].append(acct)
        if len(rows) < 100: break
        page += 1
for k, it in items.items():
    it["lists"] = sorted(lists.get(k, []))
n_stars = len(items)

# --- awesome lists ---------------------------------------------------------
BAD_OWNERS = {"topics", "sponsors", "orgs", "features", "marketplace", "collections", "apps", "about",
              "settings", "login", "join", "explore", "trending", "notifications", "security", "pricing",
              "enterprise", "site", "contact", "customer-stories", "readme", "github-copilot", "codespaces", "search"}
LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)|href=\"(https?://[^\"]+)\"")
GH = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")

def raw_readme(repo):
    for f in README_NAMES:
        r = c.get(f"https://raw.githubusercontent.com/{repo}/HEAD/{f}")
        if r.status_code == 200:
            return r.text
    return ""

def entry_text(line):
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", line)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"`|\*\*|__", "", t)
    t = re.sub(r"^\s*[-*+|]\s*", "", t)
    return re.sub(r"\s+", " ", t).strip(" |-")[:400]

external = {}
per_list = {}
for src in AWESOME:
    text = raw_readme(src)
    section, n_gh, n_ext = "", 0, 0
    for line in text.splitlines():
        h = re.match(r"^\s*#{1,6}\s+(.*)", line)
        if h:
            section = entry_text(h.group(1))[:120]
            continue
        for m in LINK.finditer(line):
            url = (m.group(2) or m.group(3)).split("#")[0].split("?")[0].rstrip("/")
            if re.search(r"shields\.io|badge|\.(png|svg|gif|jpe?g|webp)$|creativecommons", url, re.I):
                continue
            src_info = {"list": src, "section": section, "entry": entry_text(line)}
            g = GH.match(url)
            if g:
                owner, name = g.group(1), re.sub(r"\.git$", "", g.group(2))
                if owner.lower() in BAD_OWNERS: continue
                key = f"{owner}/{name}".lower()
                if key == src.lower(): continue
                it = items.setdefault(key, {"id": key, "repo": f"{owner}/{name}", "kind": "awesome",
                                            "accounts": [], "lists": [], "sources": []})
                if not any(s["list"] == src for s in it["sources"]):
                    it["sources"].append(src_info); n_gh += 1
            elif "github.com" not in url:
                e = external.setdefault(url, {"url": url, "sources": []})
                if not any(s["list"] == src for s in e["sources"]):
                    e["sources"].append(src_info); n_ext += 1
    per_list[src] = {"github": n_gh, "external": n_ext, "readme_kb": len(text) // 1024}
    print(f"{src:50} gh={n_gh:5} ext={n_ext:5}", flush=True)

rows = sorted(items.values(), key=lambda x: (x["kind"] != "star", x["id"]))
write_jsonl(DATA / "items.jsonl", rows)
write_jsonl(DATA / "external.jsonl", sorted(external.values(), key=lambda x: x["url"]))
(DATA / "collect_stats.json").write_text(json.dumps({"stars": n_stars, "items": len(rows),
    "awesome_only": len(rows) - n_stars, "external": len(external), "per_list": per_list}, indent=2))
print(f"stars={n_stars} items={len(rows)} awesome_only={len(rows)-n_stars} external={len(external)}")
