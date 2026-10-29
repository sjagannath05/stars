"""Sort data/external.jsonl into: pages to fetch, links to attach to existing repo pages, and dropped noise.

Writes:
  data/pages.jsonl         one row per URL to fetch+summarize: {id, url, kind: tool|article, sources}
  data/repo_links.jsonl    {repo_id, url, label: website|demo} to show on existing repo pages
  data/dropped.jsonl       {url, reason}
"""
import hashlib, json, re, collections
import httpx
from common import *

ARTICLE_LISTS = {"charlax/professional-programming", "practical-tutorials/project-based-learning",
                 "codecrafters-io/build-your-own-x", "usamahz/cpu-performance-engineering", "kuchin/awesome-cto",
                 "lutzh/awesome-event-driven-architecture", "hahwul/DevSecOps", "nibzard/awesome-agentic-patterns",
                 "florinpop17/app-ideas"}
NOISE = re.compile(r"(^|[/.])(twitter\.com|x\.com|linkedin\.com|discord\.com|discord\.gg|facebook\.com|instagram\.com|"
                   r"t\.me|pbs\.twimg\.com|spdx\.org|patreon\.com|ko-fi\.com|buymeacoffee\.com|opencollective\.com|"
                   r"paypal\.(com|me)|liberapay\.com|bsky\.app)(/|$)|youtube\.com/(@|c/|channel/|user/)|amazon\.[a-z.]+/", re.I)
LINK = re.compile(r"\[([^\]]*)\]\((https?://[^)\s]+)\)|href=\"(https?://[^\"]+)\"")
GH = re.compile(r"^https?://(?:www\.)?github\.com/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+)/?$")

def norm(u): return u.split("#")[0].split("?")[0].rstrip("/")
def page_id(u): return hashlib.sha1(u.encode()).hexdigest()[:16]

c = httpx.Client(timeout=30, follow_redirects=True)
E = {e["url"]: e for e in read_jsonl(DATA / "external.jsonl")}
repo_ids = {it["id"] for it in read_jsonl(DATA / "items.jsonl")}
line_repo, anchor = {}, {}
for src in AWESOME:
    text = ""
    for f in README_NAMES:
        r = c.get(f"https://raw.githubusercontent.com/{src}/HEAD/{f}")
        if r.status_code == 200: text = r.text; break
    for line in text.splitlines():
        links = [((m.group(1) or "").strip(), norm(m.group(2) or m.group(3))) for m in LINK.finditer(line)]
        repos = [f"{g.group(1)}/{re.sub(r'.git$', '', g.group(2))}".lower() for _, u in links if (g := GH.match(u))]
        repos = [r for r in repos if r in repo_ids]
        for txt, u in links:
            if u in E:
                anchor.setdefault(u, txt.lower())
                if repos: line_repo.setdefault(u, repos[0])

pages, attach, dropped = [], [], []
stats = collections.Counter()
for u, e in E.items():
    lst = e["sources"][0]["list"]; a = anchor.get(u, "")
    if NOISE.search(u):
        dropped.append({"url": u, "reason": "social/license/donate/store"}); stats["drop"] += 1; continue
    is_demo = bool(re.search(r"\bdemo\b", a))
    if u in line_repo:
        attach.append({"repo_id": line_repo[u], "url": u, "label": "demo" if is_demo else "website"})
        stats["attach_demo" if is_demo else "attach_website"] += 1; continue
    if is_demo:
        dropped.append({"url": u, "reason": "demo link without repo"}); stats["drop"] += 1; continue
    kind = "article" if lst in ARTICLE_LISTS else "tool"
    pages.append({"id": page_id(u), "url": u, "kind": kind, "anchor": a, "sources": e["sources"]})
    stats[kind] += 1

write_jsonl(DATA / "pages.jsonl", pages)
write_jsonl(DATA / "repo_links.jsonl", attach)
write_jsonl(DATA / "dropped.jsonl", dropped)
print(dict(stats), "| pages to fetch:", len(pages))
