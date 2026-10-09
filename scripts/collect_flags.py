#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
'⚠ 정보 수정' 제보 표시기

앱의 '정보 수정' 버튼으로 들어온 GitHub 이슈 중 '열려 있는 것'을 읽어
docs/data/flags.json 에 { 항목ID: {since, count, title} } 로 남긴다.
앱은 이 항목들에 '⚠ 제보 확인 중' 배지를 붙인다.
정보를 고친 뒤 이슈를 닫으면(Close) 다음 자동 실행 때 배지가 사라진다.

GitHub Actions 기본 토큰(GITHUB_TOKEN)만 쓰므로 따로 받을 키가 없다.
"""
import json
import os
import re
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs" / "data" / "flags.json"
REPO = os.environ.get("GITHUB_REPOSITORY", "chogwangwoon/gwangju-eodi")
TOKEN = os.environ.get("GITHUB_TOKEN", "")


def get(url):
    req = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": "gwangju-eodi-flags",
        **({"Authorization": f"Bearer {TOKEN}"} if TOKEN else {}),
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    flags = {}
    for page in range(1, 6):
        items = get(f"https://api.github.com/repos/{REPO}/issues?state=open&per_page=100&page={page}")
        if not items:
            break
        for it in items:
            if "pull_request" in it:
                continue
            title = it.get("title") or ""
            if not title.startswith("[정보 수정]"):
                continue
            m = re.search(r"^ID:\s*(\S+)", it.get("body") or "", re.M)
            if not m:
                continue
            fid = m.group(1).strip()
            f = flags.setdefault(fid, {"since": it["created_at"][:10], "count": 0, "title": title[:80]})
            f["count"] += 1
            f["since"] = min(f["since"], it["created_at"][:10])
        if len(items) < 100:
            break
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(flags, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"제보 확인 중인 항목 {len(flags)}개")


if __name__ == "__main__":
    main()
