#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
동네 여가 정보 · 공휴일 수집기 (주 1회면 충분 — 원본이 분기·수시 갱신)

1) 전국평생학습강좌표준데이터  → docs/data/programs.json
   구청·주민센터·도서관·평생학습관·문화센터 강좌 중 광주·전남, 아직 안 끝난 것
2) 전국도서관표준데이터        → docs/data/libraries.json
   광주·전남 공공·작은도서관의 요일별 운영시간·휴관일·좌표

공공데이터포털에서 두 데이터 모두 '활용신청' 필요 (같은 인증키 DATA_GO_KR_KEY 사용).
한쪽이 실패해도 지난번 파일을 그대로 두고, 상태만 meta.json 에 남긴다.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
import time
import urllib.parse
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import update_data as u

OUT_PROGRAMS = u.DATA_DIR / "programs.json"
OUT_LIBRARIES = u.DATA_DIR / "libraries.json"
OUT_HOLIDAYS = u.DATA_DIR / "holidays.json"
REFRESH_DAYS = 6           # 이 기간 안에 받은 파일이 있으면 건너뜀 (호출 한도 절약)
STD_BASE = "http://api.data.go.kr/openapi"
ROWS = 1000


def pick(row: dict, *names, default=""):
    """표준데이터 응답 항목 이름이 조금씩 달라도 찾아낸다."""
    low = {k.lower(): v for k, v in row.items()}
    for n in names:
        v = low.get(n.lower())
        if v not in (None, ""):
            return str(v).strip()
    return default


def std_rows(service: str, max_pages=120, budget=25 * 60, **filters):
    rows, total = [], None
    rows_n = ROWS
    deadline = time.time() + budget
    for page in range(1, max_pages + 1):
        if time.time() > deadline:
            raise RuntimeError(f"시간 예산 초과 ({len(rows):,}/{total or 0:,}건에서 중단)")
        params = {"serviceKey": u.DATA_KEY, "pageNo": str(page), "numOfRows": str(rows_n), "type": "json", **filters}
        try:
            raw = u.http_get(f"{STD_BASE}/{service}?" + urllib.parse.urlencode(params), timeout=90)
        except RuntimeError as e:
            if "HTTP 400" in str(e) and rows_n > 100 and page == 1:
                rows_n = 100                      # 한 번에 1,000건이 안 되는 서비스 → 100건씩
                print("  한 번에 받는 양을 100건으로 줄여 다시 시도", flush=True)
                params["numOfRows"] = "100"
                raw = u.http_get(f"{STD_BASE}/{service}?" + urllib.parse.urlencode(params), timeout=90)
            else:
                raise
        text = raw.decode("utf-8", "replace").strip()
        if not text.startswith("{"):
            # 인증 실패 등은 XML로 온다
            m = re.search(r"<(?:returnAuthMsg|resultMsg)>([^<]+)", text)
            if m and "NODATA" in m.group(1).upper():
                break
            raise RuntimeError(f"응답 오류: {(m.group(1) if m else text[:150])}")
        j = json.loads(text)
        resp = j.get("response") or {}
        head = resp.get("header") or {}
        code = str(head.get("resultCode", "00"))
        if code == "03":                      # NODATA
            break
        if code not in ("00", "0", "0000"):
            raise RuntimeError(f"오류 {code} {head.get('resultMsg', '')}")
        body = resp.get("body") or {}
        items = body.get("items") or []
        if isinstance(items, dict):
            items = items.get("item") or []
        if isinstance(items, dict):
            items = [items]
        rows.extend(items)
        total = int(body.get("totalCount") or 0)
        if page == 1 or page % 10 == 0:
            print(f"  {service}: {len(rows):,} / {total:,}건 받는 중", flush=True)
        if page * rows_n >= total or not items:
            break
    return rows


def recent(path: Path) -> bool:
    if not path.exists():
        return False
    meta = u.read_json(u.OUT_META, {}).get("local", {})
    at = meta.get(path.stem, {}).get("fetched_at")
    if not at:
        return False
    try:
        return (u.NOW - datetime.fromisoformat(at)).days < REFRESH_DAYS
    except ValueError:
        return False


def hhmm(v: str) -> str:
    s = re.sub(r"\D", "", v or "")
    if len(s) == 3:
        s = "0" + s
    return f"{s[:2]}:{s[2:4]}" if len(s) == 4 else ""


# ───────── 1) 평생학습 강좌 ─────────
def collect_programs():
    rows = std_rows("tn_pubr_public_lifelong_lrn_lctre_api")
    out, seen = [], set()
    for r in rows:
        addr = pick(r, "edcRdnmadr", "edcRdnmAdr", "rdnmadr", "lnmadr", "edcPlaceAddr")
        org = pick(r, "operInstitutionNm", "insttNm")
        place = pick(r, "edcPlace", "edcPlc")
        if not u.in_region(" ".join([addr, org])):
            continue
        name = pick(r, "lctreNm", "lctreNmCn")
        start = u.ymd(pick(r, "edcStartDay", "edcBgnde", "edcStartDate"))
        end = u.ymd(pick(r, "edcEndDay", "edcEndde", "edcEndDate")) or start
        if not name or (end and end < u.TODAY_ISO):
            continue
        r_start = u.ymd(pick(r, "rceptStartDate", "rceptBgnde"))
        r_end = u.ymd(pick(r, "rceptEndDate", "rceptEndde"))
        key = u.norm_title(name + org + (start or ""))
        if key in seen:
            continue
        seen.add(key)
        fee = pick(r, "lctreCost", "edcCost", "fee")
        free = fee in ("0", "0원", "무료") or "무료" in fee
        district = u.district_from_addr(addr)
        out.append({
            "id": "lrn-" + hashlib.md5(key.encode()).hexdigest()[:10],
            "name": name,
            "org": org,
            "place": place,
            "address": addr,
            "district": district,
            "start": start,
            "end": end,
            "time": " ~ ".join([t for t in (hhmm(pick(r, "edcStartTime", "edcBeginTime")),
                                            hhmm(pick(r, "edcColseTime", "edcCloseTime", "edcEndTime"))) if t]),
            "days": pick(r, "operDay", "operDayCn"),
            "target": pick(r, "edcTrgetType", "edcTrgetSe"),
            "method": pick(r, "edcMthType", "edcMthSe"),
            "capacity": pick(r, "psncpa", "edcPsncpa"),
            "fee": "무료" if free else (f"{int(fee):,}원" if fee.isdigit() else fee),
            "free": free,
            "applyStart": r_start,
            "applyEnd": r_end,
            "applyHow": pick(r, "rceptMthType", "rceptMthSe"),
            "select": pick(r, "slctnMthType", "slctnMthSe"),
            "phone": pick(r, "operPhoneNumber", "phoneNumber"),
            "url": pick(r, "homepageUrl", "hmpgAddr"),
            "desc": pick(r, "lctreCo", "lctreCn", "lctreCont")[:300],   # lctreCo = 강좌내용
            "source": "전국평생학습강좌표준데이터",
            "verified": u.TODAY_ISO,
        })
    # 접수 중 → 곧 시작 → 진행 중 순
    def order(p):
        open_now = (p["applyStart"] or "0000") <= u.TODAY_ISO <= (p["applyEnd"] or "9999")
        return (0 if open_now else 1, p["start"] or "9999", p["name"])
    out.sort(key=order)
    return out, (sorted(rows[0].keys()) if rows else [])


# ───────── 2) 도서관 ─────────
def collect_libraries():
    rows = []
    for sido in ("광주광역시", "전라남도", "전남광주통합특별시"):
        try:
            rows += std_rows("tn_pubr_public_lbrry_api", max_pages=10, ctprvnNm=sido)
        except RuntimeError as e:
            if "NODATA" not in str(e).upper():
                raise
    if not rows:   # 시도 이름 필터가 안 먹는 경우: 전체를 받아서 거른다
        rows = std_rows("tn_pubr_public_lbrry_api", max_pages=30)
    out, seen = [], set()
    for r in rows:
        sido = pick(r, "ctprvnNm")
        addr = pick(r, "rdnmadr", "lnmadr")
        if not u.in_region(" ".join([sido, addr])):
            continue
        name = pick(r, "lbrryNm")
        key = u.norm_title(name + addr)
        if not name or key in seen:
            continue
        seen.add(key)
        def span(a, b):
            o, c = hhmm(pick(r, *a)), hhmm(pick(r, *b))
            return f"{o}~{c}" if o and c and o != c else ""
        out.append({
            "id": "lib-" + hashlib.md5(key.encode()).hexdigest()[:10],
            "name": name,
            "type": pick(r, "lbrrySe"),
            "district": pick(r, "signguNm") or u.district_from_addr(addr),
            "address": addr,
            "closed": pick(r, "closeDay"),
            "weekday": span(("weekdayOperOpenHhmm",), ("weekdayOperColseHhmm", "weekdayOperCloseHhmm")),
            "saturday": span(("satOperOperOpenHhmm", "satOperOpenHhmm"), ("satOperCloseHhmm", "satOperColseHhmm")),
            "holiday": span(("holidayOperOpenHhmm",), ("holidayCloseOpenHhmm", "holidayOperCloseHhmm", "holidayOperColseHhmm")),
            "seats": pick(r, "seatCo"),
            "books": pick(r, "bookCo"),
            "phone": pick(r, "phoneNumber"),
            "url": pick(r, "homepageUrl"),
            "lat": u.to_float(pick(r, "latitude")),
            "lng": u.to_float(pick(r, "longitude")),
            "source": "전국도서관표준데이터",
            "verified": u.TODAY_ISO,
        })
    out.sort(key=lambda x: (x["district"], x["name"]))
    return out, (sorted(rows[0].keys()) if rows else [])


# ───────── 3) 공휴일 (한국천문연구원 특일정보) ─────────
# 공공데이터포털 '한국천문연구원_특일 정보' 활용신청 필요. 선거일·임시공휴일·대체공휴일까지 공식 반영.
def collect_holidays():
    out = {}
    for year in (u.NOW.year, u.NOW.year + 1):
        for month in range(1, 13):
            params = {"serviceKey": u.DATA_KEY, "solYear": str(year), "solMonth": f"{month:02d}",
                      "_type": "json", "numOfRows": "50"}
            url = "https://apis.data.go.kr/B090041/openapi/service/SpcdeInfoService/getRestDeInfo?" + urllib.parse.urlencode(params)
            text = u.http_get(url).decode("utf-8", "replace").strip()
            if not text.startswith("{"):
                m = re.search(r"<(?:returnAuthMsg|resultMsg)>([^<]+)", text)
                raise RuntimeError(m.group(1) if m else text[:150])
            body = ((json.loads(text).get("response") or {}).get("body") or {})
            items = body.get("items") or {}
            items = items.get("item", []) if isinstance(items, dict) else []
            if isinstance(items, dict):
                items = [items]
            for it in items:
                if str(it.get("isHoliday", "Y")).upper() != "Y":
                    continue
                d = u.ymd(it.get("locdate"))
                if d:
                    out[d] = str(it.get("dateName") or "공휴일").strip()
    if len(out) < 10:
        raise RuntimeError(f"공휴일이 너무 적게 왔어요({len(out)}건) — 기존 파일 유지")
    return dict(sorted(out.items())), []


def main():
    force = "--force" in sys.argv
    meta = u.read_json(u.OUT_META, {})
    local = meta.get("local", {})
    for stem, path, fn, label in [
        ("holidays", OUT_HOLIDAYS, collect_holidays, "공휴일"),          # 빠른 것부터
        ("libraries", OUT_LIBRARIES, collect_libraries, "도서관"),
        ("programs", OUT_PROGRAMS, collect_programs, "평생학습 강좌"),   # 전국 자료라 가장 오래 걸림 → 마지막
    ]:
        if not force and recent(path):
            print(f"{label}: 최근에 받아서 건너뜀")
            continue
        try:
            if not u.DATA_KEY:
                raise RuntimeError("DATA_GO_KR_KEY가 없습니다.")
            items, keys = fn()
            if not items and u.read_json(path, None):
                raise RuntimeError("0건이 와서 기존 파일을 유지합니다")
            u.write_json(path, items)
            local[stem] = {"name": label, "ok": True, "count": len(items),
                           "fetched_at": u.NOW.isoformat(timespec="seconds"),
                           "fields": keys[:40]}       # 첫 실행 때 항목 이름 확인용
        except Exception as e:
            prev = local.get(stem, {})
            local[stem] = {"name": label, "ok": False, "count": prev.get("count", 0),
                           "fetched_at": prev.get("fetched_at"),
                           "message": u.scrub(f"수집 실패 · 지난 자료 유지 ({str(e)[:200]})")}
        print(label, json.dumps({k: v for k, v in local[stem].items() if k != "fields"}, ensure_ascii=False), flush=True)
        meta = u.read_json(u.OUT_META, {})
        meta["local"] = local
        u.write_json(u.OUT_META, meta)


if __name__ == "__main__":
    main()
