"""Shared paths and helpers for the stars pipeline."""
import json, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA, CACHE, SUMS = ROOT / "data", ROOT / "cache", ROOT / "summaries"
README_DIR = CACHE / "readme"
ACCOUNTS = ["sjagannath05", "JagannathS"]
AWESOME = [
    "awesome-selfhosted/awesome-selfhosted", "vinta/awesome-python", "jaywcjlove/awesome-mac",
    "serhii-londar/open-source-mac-os-apps", "eudk/awesome-ai-tools", "nibzard/awesome-agentic-patterns",
    "lutzh/awesome-event-driven-architecture", "kuchin/awesome-cto", "hotheadhacker/awesome-selfhost-docker",
    "e2b-dev/awesome-ai-agents", "hesreallyhim/awesome-claude-code", "ibraheemdev/modern-unix",
    "codecrafters-io/build-your-own-x", "practical-tutorials/project-based-learning",
    "charlax/professional-programming", "florinpop17/app-ideas", "hahwul/DevSecOps",
    "hanishrao/collective-ai-tools", "usamahz/cpu-performance-engineering",
]
README_NAMES = ["README.md", "readme.md", "Readme.md", "README.MD", "README.rst",
                "README.markdown", "README", "README.txt", "docs/README.md"]

def load_env():
    env = {}
    p = ROOT / ".env"
    if p.exists():
        for line in p.read_text().split("\n"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    for k, v in env.items():
        os.environ.setdefault(k, v)
    return env

def slug(repo):
    return repo.lower().replace("/", "__")

def read_jsonl(p):
    p = Path(p)
    return [json.loads(l) for l in p.read_text().split("\n") if l.strip()] if p.exists() else []

def write_jsonl(p, rows):
    Path(p).write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))

def clean_markdown(t):
    t = re.sub(r"<!--[\s\S]*?-->", "", t)
    t = re.sub(r"<[^>]+>", " ", t)
    t = re.sub(r"!\[[^\]]*\]\([^)]*\)", "", t)
    t = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", t)
    t = re.sub(r"https?://\S+", "", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n\s*\n(\s*\n)+", "\n\n", t)
    return t.strip()
