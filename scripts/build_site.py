"""Build the static site into docs/ (served by GitHub Pages at /stars/).

  python3 build_site.py
Reads summaries/*.json and data/repo_links.jsonl; writes docs/index.html, docs/r/<id>.html, docs/assets/*.
"""
import html, json, math, re, shutil, time, collections
from common import *

DOCS = ROOT / os.environ.get("STARS_SITE_OUT", "_site")   # build output; deployed by GitHub Actions, not committed
SITE_SRC = ROOT / "site"
OWNER_URL = "https://github.com/sjagannath05"
FONTS = ('<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
         '<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Atkinson+Hyperlegible:wght@400;700'
         '&family=Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=JetBrains+Mono:wght@400;500&display=swap">')
e = lambda s: html.escape(str(s or ""), quote=True)

def short_list(name):
    return name.split("/")[-1]

PAGE_SUMS = ROOT / "summaries-pages"
from urllib.parse import urlparse

def domain(u):
    return urlparse(u).netloc.lower().removeprefix("www.")

def entry_name_desc(entry):
    entry = re.sub(r"(?<![A-Za-z0-9])[_*]{1,2}([^_*]+?)[_*]{1,2}(?![A-Za-z0-9])", r"\1", entry or "").strip()
    m = re.match(r"^(.{1,80}?)\s+[-–—:]\s+(.*)$", entry or "")
    return (m.group(1).strip(), m.group(2).strip()) if m else ((entry or "")[:60], entry or "")

BLOCKED = re.compile(r"^(unknown|unclear)\b|could not be (read|loaded)|couldn't be read|was blocked|blocked (access|the request|by)|"
                     r"security check|captcha|verify (you are|that you'?re) (a )?human|access denied|error page|"
                     r"(cloudflare|bot) (block|check|protection|challenge)|content is (unknown|not known)", re.I)

def load_pages():
    meta = {r["id"]: r for r in read_jsonl(DATA / "pages.jsonl")}
    done = [json.loads(p.read_text()) for p in sorted(PAGE_SUMS.glob("*.json"))] if PAGE_SUMS.exists() else []
    # summaries of bot walls / error pages are not real summaries: show those as plain link cards instead
    done = [d for d in done if not BLOCKED.search(d["summary"].get("one_liner", ""))]
    have = {d["id"] for d in done}
    unread = []
    for pid, m in meta.items():
        if pid in have: continue
        src = (m.get("sources") or [{}])[0]
        name, desc = entry_name_desc(src.get("entry", ""))
        unread.append({"id": pid, "url": m["url"], "kind": m["kind"], "name": name or domain(m["url"]),
                       "desc": desc, "section": src.get("section", ""), "sources": m.get("sources", [])})
    return done, unread

META_FILE = DATA / "repo_meta.json"

def load():
    recs = [json.loads(p.read_text()) for p in sorted(SUMS.glob("*.json"))]
    # live metadata (stars, last push, Lists, accounts, license...) lives in one file so weekly
    # refreshes change a single file instead of rewriting every summary
    meta = json.loads(META_FILE.read_text()) if META_FILE.exists() else {}
    for r in recs:
        m = meta.get(r["id"])
        if not m: continue
        r.update({k: v for k, v in m.items() if k != "license"})
        if m.get("license"):
            r["license"] = m["license"]
            if (r["summary"].get("license") or "").lower() in ("", "unknown", "noassertion", "other"):
                r["summary"]["license"] = m["license"]
    links = collections.defaultdict(dict)
    for l in read_jsonl(DATA / "repo_links.jsonl"):
        links[l["repo_id"]].setdefault(l["label"], l["url"])
    return recs, links

def similar_map(recs, k=6):
    docs = {}
    for r in recs:
        s = r["summary"]
        toks = set(s.get("keywords", [])) | {"cat:" + s.get("category", "").lower()}
        docs[r["id"]] = toks
    df = collections.Counter(t for toks in docs.values() for t in toks)
    n = len(docs)
    idf = {t: math.log(n / c) for t, c in df.items() if 1 < c < n * 0.05}
    inv = collections.defaultdict(list)
    for i, toks in docs.items():
        for t in toks:
            if t in idf: inv[t].append(i)
    out = {}
    for i, toks in docs.items():
        sc = collections.Counter()
        for t in toks:
            if t not in idf: continue
            for j in inv[t]:
                if j != i: sc[j] += idf[t]
        out[i] = [j for j, _ in sc.most_common(k)]
    return out

CMD = re.compile(r"^\s*(\$ )?(pip3?|pipx|uv|npm|npx|pnpm|yarn|bun|brew|docker|docker-compose|podman|git|go|cargo|curl|wget|python3?|"
                 r"node|deno|make|cmake|apt(-get)?|dnf|yum|pacman|snap|flatpak|kubectl|helm|sudo|bash|sh|chmod|mkdir|cd|export|"
                 r"\./|winget|choco|scoop|composer|gem|bundle|mix|dotnet|mvn|gradle|ollama|conda|poetry|swift|xcodebuild|mas)\b")

def looks_like_command(t):
    if CMD.match(t): return True
    # short, lowercase-led, no sentence punctuation: e.g. "scrapling install", "ollama run llama3"
    return bool(re.match(r"^[a-z0-9_./-]+( [^\s]+){0,8}$", t)) and len(t) < 90 and ". " not in t

def run_item(text):
    t = text.strip().strip("`")
    if looks_like_command(t):
        m = re.match(r"^(.*?)\s+\(([^()]*)\)\s*$", t)
        cmd, note = (m.group(1), m.group(2)) if m and not looks_like_command(m.group(2)) else (t, "")
        return (f'<div class="run"><pre><code>{e(cmd)}</code></pre><button class="copy" type="button">Copy</button>'
                + (f'<p class="note">{e(note)}</p>' if note else "") + "</div>")
    t = re.sub(r"`([^`]+)`", lambda m: f"<code>{e(m.group(1))}</code>", e(t))
    return f'<p class="note">{t}</p>'

ASSET_V = {}
PRELOAD = [""]

def asset_version(name):
    """Short content hash of site/<name> (or the built index.js), used as ?v= to bust browser caches."""
    if name not in ASSET_V:
        import hashlib
        p = (DOCS / "assets" / name) if name in ("index.js", "index.json") else (SITE_SRC / name)
        ASSET_V[name] = hashlib.sha1(p.read_bytes()).hexdigest()[:8] if p.exists() else "0"
    return ASSET_V[name]

def head(title, desc, depth):
    pre = "../" * depth
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1,viewport-fit=cover">
<title>{e(title)}</title><meta name="description" content="{e(desc)}">
<meta name="color-scheme" content="light dark">
<link rel="icon" href="data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 32 32'%3E%3Crect width='32' height='32' rx='8' fill='%23b7791f'/%3E%3Ctext x='16' y='23' font-size='20' text-anchor='middle' fill='white'%3E%E2%98%85%3C/text%3E%3C/svg%3E">
{FONTS}{PRELOAD[0] if depth == 0 else ""}<link rel="stylesheet" href="{pre}assets/style.css?v={asset_version("style.css")}"><script src="{pre}assets/theme.js?v={asset_version("theme.js")}"></script></head><body>"""

ACCT_COUNTS = {}

def topbar(depth):
    pre = "../" * depth
    items = "".join(f'<a href="https://github.com/{a}?tab=stars"><span>@{a}</span><small>{ACCT_COUNTS.get(a, "")} stars</small></a>' for a in ACCOUNTS)
    return f"""<header class="wrap bar"><a class="brand" href="{pre}index.html"><span class="mark" aria-hidden="true">★</span>Starchive</a>
<nav><details class="gh"><summary class="iconbtn">GitHub <span aria-hidden="true">▾</span></summary><div class="menu">{items}</div></details>
<button class="iconbtn" id="theme" type="button" aria-label="Toggle dark mode">◐<span class="tl"> Theme</span></button></nav></header>"""

def footer(gen):
    return f"""<footer class="wrap foot">Summaries are generated by an AI model (Claude Haiku) from each project's README or web page and its listing,
and can contain mistakes — check the project before relying on it. Built {gen}. A personal index of stars from {" and ".join(f'<a href="https://github.com/{a}?tab=stars">@{a}</a>' for a in ACCOUNTS)}.</footer></body></html>"""

def repo_page(r, links, sim, byid, gen):
    s = r["summary"]; owner, name = r["repo"].split("/", 1)
    site = links.get("website") or r.get("homepage") or ""
    demo = links.get("demo", "")
    actions = [f'<a class="btn primary" href="https://github.com/{e(r["repo"])}">View on GitHub ↗</a>']
    if site: actions.append(f'<a class="btn" href="{e(site)}">Website ↗</a>')
    if demo: actions.append(f'<a class="btn" href="{e(demo)}">Live demo ↗</a>')
    facts = [("Category", e(s.get("category", "")))]
    if r.get("stars"): facts.append(("Stars", f'★ {r["stars"]:,}'))
    if r.get("language"): facts.append(("Language", e(r["language"])))
    lic = s.get("license") or r.get("license") or ""
    if lic and lic.lower() not in ("unknown", "noassertion"): facts.append(("License", e(lic)))
    if r.get("pushed_at"): facts.append(("Last push", e(r["pushed_at"])))
    if r.get("archived"): facts.append(("Status", "Archived"))
    if r.get("accounts"): facts.append(("Starred by", ", ".join(f'<a href="https://github.com/{a}?tab=stars">@{e(a)}</a>' for a in r["accounts"])))
    if r.get("lists"): facts.append(("My lists", " ".join(f'<a class="chip" href="../index.html#list={e(g)}">{e(g)}</a>' for g in r["lists"])))
    found = "".join(f'<div class="src"><a href="https://github.com/{e(x["list"])}">{e(short_list(x["list"]))}</a>'
                    f'{" › " + e(x["section"]) if x.get("section") else ""}</div>' for x in r.get("sources", [])[:4])
    if found: facts.append(("Found in", found))
    sections = []
    if s.get("what_it_does"): sections.append(f'<section><h2>What it does</h2><p class="prose">{e(s["what_it_does"])}</p></section>')
    if s.get("use_cases"): sections.append('<section><h2>Use it to</h2><ul class="clean">' + "".join(f"<li>{e(u)}</li>" for u in s["use_cases"]) + "</ul></section>")
    if s.get("how_to_run"): sections.append('<section><h2>Get started</h2><div class="runs">' + "".join(run_item(x) for x in s["how_to_run"]) + "</div></section>")
    if s.get("notable"): sections.append('<section><h2>Stands out for</h2><ul class="clean">' + "".join(f"<li>{e(u)}</li>" for u in s["notable"]) + "</ul></section>")
    if s.get("stack"): sections.append('<section><h2>Built with</h2><div class="chips">' + "".join(f'<span class="chip">{e(u)}</span>' for u in s["stack"]) + "</div></section>")
    if s.get("caveats"): sections.append('<section class="caveats"><h2>Worth knowing</h2><ul class="clean">' + "".join(f"<li>{e(u)}</li>" for u in s["caveats"]) + "</ul></section>")
    if s.get("keywords"): sections.append('<section><h2>Search terms</h2><div class="kw">' + "".join(f'<a href="../index.html#q={e(k)}">{e(k)}</a>' for k in s["keywords"]) + "</div></section>")
    sims = [byid[j] for j in sim if j in byid]
    if sims:
        sections.append('<section><h2>Similar in this index</h2><div class="similar">' + "".join(sim_card(x, 1) for x in sims) + "</div></section>")
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    return (head(f'{r["repo"]} · Starchive', s.get("one_liner", ""), 1) + topbar(1) +
            f"""<main class="wrap"><div class="crumbs"><a href="../index.html" onclick="if(document.referrer.indexOf(location.host)>-1&&history.length>1){{history.back();return false}}">← Back to search</a></div>
<div class="repo-head t-r"><div class="eyebrow">{"★ In my stars" if r["kind"] == "star" else "From an awesome list"}</div>
<h1><span class="owner">{e(owner)}/</span>{e(name)}</h1><p class="lede">{e(s.get("one_liner", ""))}</p><div class="actions">{"".join(actions)}</div></div>
<div class="layout"><div>{"".join(sections)}</div><aside class="rail"><div class="facts"><dl>{dl}</dl></div></aside></div></main>""" + footer(gen))

PRICING = {"open source", "free", "freemium", "paid", "freeware"}

def clean_pricing(v):
    v = (v or "").strip().lower()
    return v if v in PRICING else ""

def page_page(p, sim, byid, gen):
    s = p["summary"]; art = p["kind"] == "article"
    s["pricing"] = clean_pricing(s.get("pricing"))
    title = s.get("title") if art else s.get("name")
    title = title or p.get("page_title") or domain(p["url"])
    url = p.get("final_url") or p["url"]
    actions = [f'<a class="btn primary" href="{e(p["url"])}">{"Read it" if art else "Visit site"} ↗</a>']
    facts = [("Topic" if art else "Category", e(s.get("category", "")))]
    if art:
        if s.get("format"): facts.append(("Format", e(s["format"])))
        if s.get("level") and s["level"] != "unknown": facts.append(("Level", e(s["level"])))
        if s.get("technologies"): facts.append(("Tech", e(", ".join(s["technologies"][:8]))))
    else:
        if s.get("pricing"): facts.append(("Pricing", e(s["pricing"])))
        if s.get("platforms"): facts.append(("Platforms", e(", ".join(s["platforms"][:8]))))
    facts.append(("Site", f'<a href="{e(url)}">{e(domain(url))}</a>'))
    if p.get("method") == "wayback": facts.append(("Note", "Summarized from an archived copy; the live page may be gone."))
    found = "".join(f'<div class="src"><a href="https://github.com/{e(x["list"])}">{e(short_list(x["list"]))}</a>'
                    f'{" › " + e(x["section"]) if x.get("section") else ""}</div>' for x in p.get("sources", [])[:3])
    if found: facts.append(("Found in", found))
    sec = []
    body = s.get("summary") if art else s.get("what_it_does")
    if body: sec.append(f'<section><h2>{"Summary" if art else "What it does"}</h2><p class="prose">{e(body)}</p></section>')
    pts = s.get("takeaways") if art else s.get("use_cases")
    if pts: sec.append(f'<section><h2>{"Key takeaways" if art else "Use it to"}</h2><ul class="clean">' + "".join(f"<li>{e(x)}</li>" for x in pts) + "</ul></section>")
    if not art and s.get("notable"): sec.append('<section><h2>Stands out for</h2><ul class="clean">' + "".join(f"<li>{e(x)}</li>" for x in s["notable"]) + "</ul></section>")
    if not art and s.get("caveats"): sec.append('<section class="caveats"><h2>Worth knowing</h2><ul class="clean">' + "".join(f"<li>{e(x)}</li>" for x in s["caveats"]) + "</ul></section>")
    if s.get("keywords"): sec.append('<section><h2>Search terms</h2><div class="kw">' + "".join(f'<a href="../index.html#q={e(k)}">{e(k)}</a>' for k in s["keywords"]) + "</div></section>")
    sims = [byid[j] for j in sim if j in byid]
    if sims: sec.append('<section><h2>Similar in this index</h2><div class="similar">' + "".join(sim_card(x, 1) for x in sims) + "</div></section>")
    dl = "".join(f"<dt>{k}</dt><dd>{v}</dd>" for k, v in facts)
    eyebrow = ("Article" if art else "Tool / site") + (f' · {e(s.get("format"))}' if art and s.get("format") not in (None, "", "article") else "")
    return (head(f"{title} · Starchive", s.get("one_liner", ""), 1) + topbar(1) +
            f"""<main class="wrap"><div class="crumbs"><a href="../index.html" onclick="if(document.referrer.indexOf(location.host)>-1&&history.length>1){{history.back();return false}}">← Back to search</a></div>
<div class="repo-head {"t-a" if art else "t-w"}"><div class="eyebrow">{eyebrow}</div><h1 class="title">{e(title)}</h1><p class="lede">{e(s.get("one_liner", ""))}</p><div class="actions">{"".join(actions)}</div></div>
<div class="layout"><div>{"".join(sec)}</div><aside class="rail"><div class="facts"><dl>{dl}</dl></div></aside></div></main>""" + footer(gen))

def sim_card(x, depth):
    pre = "" if depth == 1 else "r/"
    if x["_t"] == "repo":
        href = f'{"../r/" if depth == 1 else "r/"}{e(slug(x["repo"]))}.html'; name = x["repo"]
    else:
        href = f'{"../p/" if depth == 1 else "p/"}{e(x["id"])}.html'
        sm = x["summary"]; name = sm.get("title") or sm.get("name") or domain(x["url"])
    return f'<a href="{href}"><div class="n">{e(name)}</div><div class="o">{e(x["summary"].get("one_liner",""))}</div></a>'

STATS = []
INLINE = [""]

def index_page(n_all, n_mine, gen):
    tries = ["self-host a file share", "scrape a protected site", "mac clipboard manager", "kubernetes log viewer",
             "run an llm on my phone", "text to speech", "password manager", "pdf to markdown"]
    return (head("Starchive", "Every repository I've starred on GitHub, plus the tools and articles in the awesome lists I follow, searchable by what they do.", 0) + topbar(0) +
            f"""<main class="wrap"><section class="hero"><div class="sky" aria-hidden="true"></div><h1>Everything I've starred, <em>searchable by what it does</em>.</h1>
<p>{n_all:,} repos, tools and articles from my GitHub stars and the awesome lists I follow, each one read and summarized. Describe the job in plain words and the right one comes up.</p>
<div class="searchbox"><svg class="glass" width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" aria-hidden="true"><circle cx="11" cy="11" r="7"/><path d="m20 20-3.5-3.5"/></svg>
<input id="q" type="search" placeholder="e.g. share files with my team" autocomplete="off" spellcheck="false" aria-label="Search repositories"><kbd>/</kbd></div>
<div class="loadbar" id="loadbar" role="status" aria-live="polite" hidden><div class="track"><div class="fill" id="loadfill"></div></div><span id="loadtext">Loading the full index…</span></div>
<div class="tries"><span>Try</span>{"".join(f'<button class="try" type="button">{e(t)}</button>' for t in tries)}</div>
<dl class="stats">{"".join(f'<div class="stat {c}"><dt>{e(lbl)}</dt><dd>{n:,}</dd></div>' for c, lbl, n in STATS)}</dl></section>
<div class="filters"><div class="seg" role="group" aria-label="Source">
<button type="button" data-src="all">All<small id="n-all"></small></button><button type="button" data-src="mine">My stars<small id="n-mine"></small></button><button type="button" data-src="awesome">Awesome repos<small id="n-aw"></small></button><button type="button" data-src="tool">Tools &amp; sites<small id="n-tool"></small></button><button type="button" data-src="article">Articles<small id="n-art"></small></button></div>
<label class="sr-only" for="list">My list</label><select id="list"></select></div>
<div class="refine chips" id="refine"></div>
<div class="summary-line"><span id="count"></span><button class="iconbtn" id="clear" type="button" hidden>Clear</button></div>
<section class="browse" id="browse" hidden><h2>Browse by category</h2><div class="cat-grid" id="catgrid"></div></section>
<ol class="results" id="results"></ol><button class="more" id="more" type="button" hidden></button></main>
<script>{INLINE[0]}</script><script src="assets/app.js?v={asset_version("app.js")}"></script>""" + footer(gen))

def main():
    recs, links = load()
    pages, unread = load_pages()
    for r in recs: r["_t"] = "repo"; r["_key"] = r["id"]
    for p in pages: p["_t"] = p["kind"]; p["_key"] = "page:" + p["id"]
    allrec = recs + pages
    byid = {x["_key"]: x for x in allrec}
    for x in allrec: x["id_"] = x["id"]; x["id"] = x["_key"]
    sims = similar_map(allrec)
    for x in allrec: x["id"] = x["id_"]
    gen = time.strftime("%d %b %Y")
    for a in ACCOUNTS:
        ACCT_COUNTS[a] = sum(1 for r in recs if a in (r.get("accounts") or []))
    n_star = sum(1 for r in recs if r["kind"] == "star")
    STATS[:] = [("t-r", "My stars", n_star), ("t-r2", "Awesome repos", len(recs) - n_star),
                ("t-w", "Tools & sites", sum(1 for p in pages if p["kind"] != "article") + sum(1 for u in unread if u["kind"] != "article")),
                ("t-a", "Articles", sum(1 for p in pages if p["kind"] == "article") + sum(1 for u in unread if u["kind"] == "article"))]
    for d in ("r", "p", "assets"): (DOCS / d).mkdir(parents=True, exist_ok=True)
    for f in ["style.css", "app.js", "theme.js"]:
        shutil.copyfile(SITE_SRC / f, DOCS / "assets" / f)
    idx = []
    def via_of(src):
        return (f'{short_list(src[0]["list"])}{" › " + src[0]["section"] if src[0].get("section") else ""}' if src else "")[:80]
    for r in recs:
        s = r["summary"]; owner, name = r["repo"].split("/", 1)
        idx.append({"id": slug(r["repo"]), "t": "r", "n": name, "d": owner, "o": s.get("one_liner", "")[:220], "c": s.get("category", ""),
                    "k": s.get("keywords", [])[:24], "u": s.get("use_cases", [])[:3], "g": r.get("lists", []),
                    "m": 1 if r["kind"] == "star" else 0, "s": r.get("stars", 0), "l": r.get("language", ""), "v": via_of(r.get("sources", []))})
        (DOCS / "r" / f'{slug(r["repo"])}.html').write_text(repo_page(r, links.get(r["id"], {}), sims.get(r["_key"], []), byid, gen))
    for p in pages:
        s = p["summary"]; art = p["kind"] == "article"
        title = (s.get("title") if art else s.get("name")) or p.get("page_title") or domain(p["url"])
        idx.append({"id": p["id"], "t": "a" if art else "w", "n": title, "d": domain(p.get("final_url") or p["url"]),
                    "o": s.get("one_liner", "")[:220], "c": s.get("category", ""), "k": s.get("keywords", [])[:18],
                    "u": (s.get("takeaways") if art else s.get("use_cases") or [])[:2], "g": [], "m": 0, "s": 0,
                    "l": s.get("format", "") if art else clean_pricing(s.get("pricing")),
                    "v": via_of(p.get("sources", []))})
        (DOCS / "p" / f'{p["id"]}.html').write_text(page_page(p, sims.get(p["_key"], []), byid, gen))
    for u in unread:  # no page of our own: the card links straight to the site
        idx.append({"id": u["id"], "t": "a" if u["kind"] == "article" else "w", "n": u["name"], "d": domain(u["url"]),
                    "o": u["desc"], "c": u["section"].lower()[:40], "k": [], "u": [], "g": [], "m": 0, "s": 0, "l": "",
                    "v": via_of(u.get("sources", [])), "x": u["url"]})
    full = json.dumps(idx, ensure_ascii=False, separators=(",", ":"))
    (DOCS / "assets" / "index.json").write_text(full)
    old_js = DOCS / "assets" / "index.js"
    if old_js.exists(): old_js.write_text("window.STARS=null;")  # retired; kept tiny for old cached pages
    mine = [x for x in idx if x["t"] == "r" and x["m"]]
    generic = re.compile(r"^(links?|misc(ellaneous)?|other|others|resources?|writing|general|tools?|apps?|external links|related|contents?|"
                         r"table of contents|about|license|contributing|reading|articles?|books?|videos?|talks?)$")
    cats = collections.Counter(x["c"].lower() for x in idx if x.get("c") and not x.get("x") and not generic.match(x["c"].lower()))
    meta = {"url": f"assets/index.json?v={asset_version('index.json')}", "bytes": len(full.encode()), "total": len(idx),
            "counts": {"mine": len(mine), "awesome": sum(1 for x in idx if x["t"] == "r" and not x["m"]),
                       "tool": sum(1 for x in idx if x["t"] == "w"), "article": sum(1 for x in idx if x["t"] == "a")},
            "browse": cats.most_common(24)}
    js = lambda o: json.dumps(o, ensure_ascii=False, separators=(",", ":")).replace("</", "<\\/")
    INLINE[0] = f"window.STARS_MINE={js(mine)};window.STARS_META={js(meta)};"
    PRELOAD[0] = f'<link rel="preload" href="{meta["url"]}" as="fetch" crossorigin>'
    n_mine = sum(1 for r in recs if r["kind"] == "star")
    (DOCS / "index.html").write_text(index_page(len(idx), n_mine, gen))
    (DOCS / ".nojekyll").write_text("")
    size = sum(p.stat().st_size for p in DOCS.rglob("*") if p.is_file())
    print(f"built {len(recs)} repo pages, {len(pages)} tool/article pages, {len(unread)} link-only cards "
          f"into {DOCS.name}/ ({size/1e6:.1f} MB, index.json {(DOCS/'assets'/'index.json').stat().st_size/1e6:.1f} MB, "
          f"inline stars {len(INLINE[0])/1e3:.0f} KB)")

if __name__ == "__main__":
    main()
