"""Fill in licenses for repos whose summary says 'unknown', by reading the repo's LICENSE file.

No GitHub token needed (uses raw.githubusercontent.com). Updates summaries/*.json in place.
"""
import asyncio, json, re, time
import httpx
from common import *

NAMES = ["LICENSE", "LICENSE.md", "LICENSE.txt", "LICENCE", "LICENCE.md", "COPYING", "COPYING.md",
         "license", "license.md", "License.md", "LICENSE-MIT", "LICENSE-APACHE", "UNLICENSE"]
RULES = [  # (spdx, regex) — first match wins; order matters
    ("AGPL-3.0", r"GNU AFFERO GENERAL PUBLIC LICENSE"),
    ("LGPL-3.0", r"GNU LESSER GENERAL PUBLIC LICENSE\s+Version 3"),
    ("LGPL-2.1", r"GNU (LESSER|LIBRARY) GENERAL PUBLIC LICENSE\s+Version 2"),
    ("GPL-3.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 3"),
    ("GPL-2.0", r"GNU GENERAL PUBLIC LICENSE\s+Version 2"),
    ("MPL-2.0", r"Mozilla Public License,? (Version|v\.?) ?2\.0"),
    ("Apache-2.0", r"Apache License,?\s+Version 2\.0"),
    ("EUPL-1.2", r"EUROPEAN UNION PUBLIC LICEN[CS]E v\.? ?1\.2"),
    ("Unlicense", r"This is free and unencumbered software released into the public domain"),
    ("CC0-1.0", r"CC0 1\.0|Creative Commons Zero"),
    ("CC-BY-SA-4.0", r"Attribution-ShareAlike 4\.0"),
    ("CC-BY-NC-4.0", r"Attribution-NonCommercial"),
    ("CC-BY-4.0", r"Creative Commons Attribution 4\.0|Attribution 4\.0 International"),
    ("BSL-1.1", r"Business Source License 1\.1"),
    ("ISC", r"Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee"),
    ("0BSD", r"Permission to use, copy, modify, and/or distribute this software for any purpose with or without fee is hereby granted\.\s+THE SOFTWARE"),
    ("BSD-3-Clause", r"Neither the name of .{0,120}? nor the names of"),
    ("BSD-2-Clause", r"Redistributions in binary form must reproduce the above copyright notice"),
    ("MIT", r"Permission is hereby granted, free of charge, to any person obtaining a copy"),
    ("WTFPL", r"DO WHAT THE FUCK YOU WANT TO PUBLIC LICENSE"),
    ("Zlib", r"This software is provided 'as-is', without any express or implied\s+warranty"),
]

def detect(text):
    t = re.sub(r"\s+", " ", text[:20000])
    for spdx, rx in RULES:
        if re.search(rx, t, re.I):
            return spdx
    return None

def unknown(lic):
    return not lic or lic.strip().lower() in ("unknown", "noassertion", "other", "none", "")

def mark(path, rec):
    rec["license_checked"] = time.strftime("%Y-%m-%d")
    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))

async def one(client, sem, path):
    rec = json.loads(path.read_text())
    async with sem:
        for n in NAMES:
            try:
                r = await client.get(f"https://raw.githubusercontent.com/{rec['repo']}/HEAD/{n}")
            except httpx.HTTPError:
                return None
            if r.status_code == 200 and r.text.strip():
                spdx = detect(r.text)
                if spdx:
                    rec["summary"]["license"] = spdx
                    rec["license_source"] = n
                    path.write_text(json.dumps(rec, ensure_ascii=False, indent=1))
                    return spdx
                mark(path, rec); return "unrecognized"
    mark(path, rec); return "no license file"

async def main():
    todo = []
    for p in SUMS.glob("*.json"):
        r = json.loads(p.read_text())
        if unknown(r["summary"].get("license")) and not r.get("license_checked"):
            todo.append(p)
    print("repos with unknown license:", len(todo))
    sem = asyncio.Semaphore(24)
    async with httpx.AsyncClient(timeout=20, follow_redirects=True) as c:
        res = await asyncio.gather(*(one(c, sem, p) for p in todo))
    from collections import Counter
    print(Counter(r for r in res).most_common(20))

asyncio.run(main())
