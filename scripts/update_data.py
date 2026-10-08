#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
광주·전남 자동 일정 수집기
1) docs/index.html 안의 기존 수동 일정을 안전하게 읽음
2) 한국관광공사 TourAPI 행사정보 + KOPIS 공연목록 수집
3) 수동 일정을 우선 보존하면서 API 데이터 병합
4) docs/data/events.json, meta.json 생성

중요: 실제 API 인증키는 코드에 넣지 않고 GitHub Secrets에서만 읽습니다.
"""

from __future__ import annotations
import os, json, re, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parents[1]
INDEX = ROOT / "docs" / "index.html"
DATA_DIR = ROOT / "docs" / "data"
OUT_EVENTS = DATA_DIR / "events.json"
OUT_META = DATA_DIR / "meta.json"

DATA_KEY = urllib.parse.unquote( os.environ.get("DATA_GO_KR_KEY", "").strip())
KOPIS_KEY = os.environ.get("KOPIS_API_KEY", "").strip()

KST = timezone(timedelta(hours=9))
NOW = datetime.now(KST)
TODAY = "20260101"
TO_DATE = "20261231"
KOPIS_FROM = NOW.strftime("%Y%m%d")
KOPIS_TO = (NOW + timedelta(days=30)).strftime("%Y%m%d")
TODAY_ISO = NOW.strftime("%Y-%m-%d")

# TourAPI 지역코드: 광주광역시 5 / 전라남도 38
TOUR_AREAS = [("광주", "5")]

# KOPIS 지역(시도)코드: 광주광역시 29 / 전라남도 46
KOPIS_AREAS = [("광주", "29"), ("전남", "46")]

UA = "gwangju-eodi/1.0 GitHubActions"

def read_json(path: Path, default):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default

def write_json(path: Path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8")

def embedded_manual_events():
    """현재 index.html에 들어 있는 수동 데이터를 읽는다."""
    html = INDEX.read_text(encoding="utf-8")
    token = "window.__DATA__="
    i = html.find(token)
    if i < 0:
        return []
    i += len(token)

    # JSON 객체의 중괄호 균형을 직접 추적한다.
    depth = 0
    in_string = False
    escape = False
    start = None
    end = None
    for pos in range(i, len(html)):
        ch = html[pos]
        if start is None:
            if ch == "{":
                start = pos
                depth = 1
            continue
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = pos + 1
                break

    if start is None or end is None:
        return []
    data = json.loads(html[start:end])
    # 기존 수동작성 자료만 원본으로 사용한다.
    return [e for e in data.get("events", []) if e.get("origin", "manual") == "manual"]

def http_get(url: str, timeout=30) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()

def ymd(v):
    s = re.sub(r"\D", "", str(v or ""))
    if len(s) >= 8:
        return f"{s[:4]}-{s[4:6]}-{s[6:8]}"
    return None

def district_from_addr(addr: str):
    a = addr or ""
    m = re.search(r"(광산구|동구|서구|남구|북구|[가-힣]+시|[가-힣]+군)", a)
    return m.group(1) if m else ""

def norm_title(s: str):
    return re.sub(r"[^0-9a-z가-힣]", "", (s or "").lower())

def dedup_key(e):
    api_id = str(e.get("apiId") or "").strip()
    if api_id:
        return f"{e.get('origin','api')}:{api_id}"
    return f"{norm_title(e.get('title',''))}:{e.get('start','')}"
def clean_api_text(value):
    if not value:
        return ""

    text = str(value)
    text = re.sub(r"<br\s*/?>", " / ", text, flags=re.IGNORECASE)
    text = re.sub(r"<[^>]+>", "", text)
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"\s+", " ", text)

    return text.strip()


def extract_api_url(value):
    if not value:
        return ""

    text = str(value).strip()

    match = re.search(r'href=["\']([^"\']+)["\']', text, flags=re.IGNORECASE)
    if match:
        return match.group(1).strip()

    return text

def tourapi_detail_intro(content_id, content_type_id="15"):
    params = {
        "serviceKey": DATA_KEY,
        "MobileOS": "ETC",
        "MobileApp": "gwangju-eodi",
        "_type": "json",
        "contentId": content_id,
        "contentTypeId": content_type_id,
    }

    url = (
        "https://apis.data.go.kr/B551011/KorService2/detailIntro2?"
        + urllib.parse.urlencode(params)
    )

    raw = http_get(url)
    j = json.loads(raw.decode("utf-8"))

    resp = (j or {}).get("response") or {}
    body = resp.get("body") or {}
    items = (body.get("items") or {}).get("item") or []

    if isinstance(items, list):
        return items[0] if items else {}

    if isinstance(items, dict):
        return items

    return {}
def tourapi_detail_common(content_id, content_type_id="15"):
    params = {
        "serviceKey": DATA_KEY,
        "MobileOS": "ETC",
        "MobileApp": "gwangju-eodi",
        "_type": "json",
        "contentId": content_id,
        "contentTypeId": content_type_id,
        "defaultYN": "Y",
        "firstImageYN": "Y",
        "areacodeYN": "Y",
        "catcodeYN": "N",
        "addrinfoYN": "Y",
        "mapinfoYN": "Y",
        "overviewYN": "Y",
    }

    url = (
        "https://apis.data.go.kr/B551011/KorService2/detailCommon2?"
        + urllib.parse.urlencode(params)
    )

    raw = http_get(url)
    j = json.loads(raw.decode("utf-8"))

    resp = (j or {}).get("response") or {}
    body = resp.get("body") or {}
    items = (body.get("items") or {}).get("item") or []

    if isinstance(items, list):
        return items[0] if items else {}

    if isinstance(items, dict):
        return items

    return {} 
def tourapi_detail_images(content_id):
    params = {
        "serviceKey": DATA_KEY,
        "MobileOS": "ETC",
        "MobileApp": "gwangju-eodi",
        "_type": "json",
        "contentId": content_id,
        "imageYN": "Y",
        "subImageYN": "Y",
    }

    url = (
        "https://apis.data.go.kr/B551011/KorService2/detailImage2?"
        + urllib.parse.urlencode(params)
    )

    raw = http_get(url)
    j = json.loads(raw.decode("utf-8"))

    resp = (j or {}).get("response") or {}
    body = resp.get("body") or {}
    items = (body.get("items") or {}).get("item") or []

    if isinstance(items, dict):
        items = [items]

    return items if isinstance(items, list) else []
def tourapi_festivals():
    if not DATA_KEY:
        raise RuntimeError("DATA_GO_KR_KEY가 없습니다.")

    out = []
    for area_name, area_code in TOUR_AREAS:
        params = {
            "serviceKey": DATA_KEY,
            "MobileOS": "ETC",
            "MobileApp": "gwangju-eodi",
            "_type": "json",
            "numOfRows": "1000",
            "pageNo": "1",
            "arrange": "A",
            #"areaCode": area_code,
            "eventStartDate": TODAY,
            "eventEndDate": TO_DATE,
        }
        url = "https://apis.data.go.kr/B551011/KorService2/searchFestival2?" + urllib.parse.urlencode(params)
        raw = http_get(url)
        j = json.loads(raw.decode("utf-8"))

        resp = (j or {}).get("response") or {}
        header = resp.get("header") or {}
        if str(header.get("resultCode")) not in ("0000", "0"):
            raise RuntimeError(f"TourAPI {area_name} 오류: {header}")

        body = resp.get("body") or {}
        items = (body.get("items") or {}).get("item") or []
        if isinstance(items, dict):
            items = [items]

        for x in items:
            title = (x.get("title") or "").strip()
            if not title:
                continue
            start = ymd(x.get("eventstartdate"))
            end = ymd(x.get("eventenddate")) or start
            addr = (x.get("addr1") or "").strip()
            venue_raw = (x.get("addr2") or "").strip()
            venue = venue_raw if venue_raw and venue_raw not in addr else (district_from_addr(addr) or area_name)
            if not ("광주" in addr or "전남" in addr or "전라남도" in addr):
             continue
            content_id = str(x.get("contentid") or "")
            try:
                intro = tourapi_detail_intro(content_id)
            except Exception:
                intro = {}

            try:
                common = tourapi_detail_common(content_id)
            except Exception:
                common = {}
            try:
                images = tourapi_detail_images(content_id)
            except Exception:
                images = []
                out.append({
                "id": f"tour-{content_id}",
                "apiId": content_id,
                "category": "축제",
                "subtype": "관광공사 행사",
                "title": title,
                "venue": (intro.get("eventplace") or venue).strip(),
                "district": district_from_addr(addr),
                "address": addr,
                "date": f"{start or ''} ~ {end or ''}".strip(" ~"),
                "time": clean_api_text(intro.get("playtime")) or "공식 상세 확인",
                "price": clean_api_text(intro.get("usetimefestival")) or "공식 상세 확인",
                "age": clean_api_text(intro.get("agelimit")),
                "program": clean_api_text(intro.get("program")),
                "booking": clean_api_text(intro.get("bookingplace")),
                "parking": clean_api_text(intro.get("parking")),
                "spendtime": clean_api_text(intro.get("spendtimefestival")),
                "sponsor": clean_api_text(intro.get("sponsor1") or intro.get("sponsor2")),
                "parking": (intro.get("parking") or "").strip(),
                "status": "예정" if start and start > TODAY_ISO else "진행중",
                "start": start,
                "end": end,
                "image": x.get("firstimage") or x.get("firstimage2") or common.get("firstimage") or common.get("firstimage2") or (images[0].get("originimgurl") if images else "") or "",
                "lat": float(x["mapy"]) if x.get("mapy") else None,
                "lng": float(x["mapx"]) if x.get("mapx") else None,
                "contact": intro.get("sponsor1tel") or intro.get("sponsor2tel") or x.get("tel") or "",
                "description": clean_api_text(common.get("overview")),
                "source": "한국관광공사 TourAPI",
                "url": extract_api_url(intro.get("eventhomepage") or common.get("homepage")),
                "origin": "tourapi",
                "verified": TODAY_ISO,
               "tags": ["축제", "광주" if "광주광역시" in addr else "전남"],
            })
    return out

def kopis_performances():
    if not KOPIS_KEY:
        raise RuntimeError("KOPIS_API_KEY가 없습니다.")

    out = []
    base = "http://www.kopis.or.kr/openApi/restful/pblprfr"

    for area_name, code in [("전국", "")]: 
        params = {
            "service": KOPIS_KEY,
            "stdate": KOPIS_FROM,
            "eddate": KOPIS_TO,
            "cpage": "1",
            "rows": "100",
           # "signgucode": code,
        }
        url = base + "?" + urllib.parse.urlencode(params)
        root = ET.fromstring(http_get(url))

        for db in root.findall(".//db"):
            get = lambda tag: (db.findtext(tag) or "").strip()
            mid = get("mt20id")
            title = get("prfnm")
            if not title:
                continue
            start = ymd(get("prfpdfrom"))
            end = ymd(get("prfpdto")) or start
            genre = get("genrenm") or "공연"
            venue = get("fcltynm")
            area = get("area")

            if "광주" not in area and "전남" not in area and "전라남" not in area:
             continue
            out.append({
                "id": f"kopis-{mid}",
                "apiId": mid,
                "category": "공연",
                "subtype": genre,
                "title": title,
                "venue": venue,
                "district": "",
                "date": f"{start or ''} ~ {end or ''}".strip(" ~"),
                "time": "KOPIS 상세 확인",
                "price": "KOPIS 상세 확인",
                "status": get("prfstate") or ("예정" if start and start > TODAY_ISO else "진행중"),
                "start": start,
                "end": end,
                "image": get("poster"),
                "source": "KOPIS 공연예술통합전산망",
                "url": f"https://www.kopis.or.kr/por/db/pblprfr/pblprfrView.do?menuId=MNU_00020&mt20Id={mid}",
                "origin": "kopis",
                "verified": TODAY_ISO,
                "tags": ["공연", genre, area_name],
            })
    return out
def smart_event_key(e):
    title = norm_title(e.get("title", ""))

    # 행사명 앞에 붙는 회차/연도 표현 제거
    title = re.sub(r"^(?:20\d{2}|제?\d+회)+", "", title)

    # 날짜는 같은 행사 판정에 같이 사용
    start = e.get("start", "")
    end = e.get("end", "")

    return f"{title}:{start}:{end}"
def merge_events(manual, api_events):
    merged, seen = [], set()

    # 수동 데이터가 앞에 있으므로 같은 항목이면 수동 데이터가 우선.
    for e in manual + api_events:
        if e.get("end") and e["end"] < TODAY_ISO:
            continue

        k = smart_event_key(e)

        if k in seen:
            continue

        seen.add(k)
        merged.append(e)

    merged.sort(
        key=lambda e: (
            e.get("start") or "9999-99-99",
            e.get("title") or ""
        )
    )

    return merged

def main():
    manual = embedded_manual_events()
    previous = read_json(OUT_EVENTS, [])
    api_events = []
    statuses = []

    for source_id, source_name, fn in [
        ("tourapi", "한국관광공사 행사", tourapi_festivals),
        ("kopis", "KOPIS 공연", kopis_performances),
    ]:
        try:
            rows = fn()
            api_events.extend(rows)
            statuses.append({"id": source_id, "name": source_name, "ok": True, "count": len(rows), "message": "정상 수집"})
        except Exception as e:
            statuses.append({"id": source_id, "name": source_name, "ok": False, "count": 0, "message": str(e)[:300]})

    # API가 둘 다 실패한 경우 기존 배포 데이터가 있으면 보존.
    if api_events:
        merged = merge_events(manual, api_events)
        write_json(OUT_EVENTS, merged)
    elif previous:
        merged = previous
    else:
        merged = merge_events(manual, [])

    meta = {
        "updated_at": NOW.isoformat(timespec="seconds"),
        "auto_update": True,
        "manual_count": len(manual),
        "events_count": len(merged),
        "api_status": statuses,
        "note": "수동 데이터 우선 병합 · API 실패 시 기존 배포 데이터 유지"
    }
    write_json(OUT_META, meta)
    print(json.dumps(meta, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
