#!/usr/bin/env python3
"""핵심 법령 전문(XML)을 받아 조문 단위 corpus.jsonl 생성.

Usage:
  cd safelaw/backend && source .venv/bin/activate
  set -a && source ../.env && set +a
  PYTHONPATH=. python ../scripts/build_law_corpus.py
"""

from __future__ import annotations

import argparse
import asyncio
import json
import re
import sys
import xml.etree.ElementTree as ET
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.config import get_settings  # noqa: E402
from app.law.safety_laws import CORE_LAWS  # noqa: E402

OUT = ROOT / "data" / "law" / "corpus.jsonl"


def _local(tag: str) -> str:
    return tag.split("}")[-1]


def _text_of(el: ET.Element | None) -> str:
    if el is None:
        return ""
    parts: list[str] = []
    if el.text and el.text.strip():
        parts.append(el.text.strip())
    for c in el:
        parts.append(_text_of(c))
        if c.tail and c.tail.strip():
            parts.append(c.tail.strip())
    return "\n".join(p for p in parts if p)


def _normalize_body(body: str) -> str:
    """번호만 있는 줄 + 동일 번호 본문 줄 중복 제거 (법제처 XML 트리 잔여)."""
    if not body:
        return ""
    from app.law.corpus import normalize_law_body

    return normalize_law_body(body)


def _clean_byeol_body(body: str) -> str:
    """별표 본문 공백·표 잔여 정리."""
    t = (body or "").replace("\u3000", " ")
    t = re.sub(r"[ \t]{2,}", " ", t)
    t = re.sub(r"\|{2,}", "\n", t)
    t = re.sub(r"\n{3,}", "\n\n", t)
    t = re.sub(r" *\n *", "\n", t)
    return t.strip()


def _parse_byeol_units(root: ET.Element, law_name: str, mst: str) -> list[dict]:
    """별표단위 → corpus 행 (article_no=별표1 / 별지12)."""
    rows: list[dict] = []
    for n in root.iter():
        if _local(n.tag) != "별표단위":
            continue
        kids = {_local(c.tag): c for c in n}

        def _t(key: str) -> str:
            el = kids.get(key)
            return (el.text or "").strip() if el is not None and el.text else ""

        kind = (_t("별표구분") or "별표").strip()
        no_raw = _t("별표번호")
        if not no_raw:
            continue
        try:
            no = str(int(no_raw))
        except ValueError:
            no = no_raw.lstrip("0") or no_raw
        branch = _t("별표가지번호")
        title = _t("별표제목")
        body = _clean_byeol_body(_text_of(kids.get("별표내용")))

        def _fl_seq(link: str) -> str:
            m = re.search(r"flSeq=(\d+)", link or "")
            return m.group(1) if m else ""

        # 법제처 첨부: 이미지(표 원본) · PDF · HWP — 박스문자 파싱 대신 원본 사용
        img_fl = _fl_seq(_t("별표서식이미지파일링크"))
        pdf_fl = _fl_seq(_t("별표서식PDF파일링크"))
        hwp_fl = _fl_seq(_t("별표서식파일링크"))
        # 앱 프록시 경로 (CORS·핫링크 회피)
        image_url = f"/api/law/attach?fl_seq={img_fl}" if img_fl else ""
        pdf_url = f"/api/law/attach?fl_seq={pdf_fl}" if pdf_fl else ""
        hwp_url = f"/api/law/attach?fl_seq={hwp_fl}" if hwp_fl else ""

        if kind in ("서식", "별지서식") or "서식" in kind:
            art = f"별지{no}"
            label = f"별지 제{no}호서식"
        else:
            art = f"별표{no}"
            if branch and branch not in ("0", "00", ""):
                try:
                    art = f"별표{no}의{int(branch)}"
                except ValueError:
                    art = f"별표{no}의{branch}"
            label = f"별표 {no}"

        if len(body) < 8 and not title and not (image_url or pdf_url):
            continue

        rows.append(
            {
                "law_name": law_name,
                "mst": mst,
                "article_no": art,
                "title": title or label,
                "body": body,
                "kind": "byeol",
                "image_url": image_url,
                "pdf_url": pdf_url,
                "hwp_url": hwp_url,
                "image_fl": img_fl,
                "pdf_fl": pdf_fl,
                "hwp_fl": hwp_fl,
            }
        )
    return rows


def parse_law_xml(xml_text: str, law_name: str, mst: str) -> list[dict]:
    if not xml_text.strip() or "<html" in xml_text[:200].lower():
        return []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError:
        return []

    # 응답 속 정식 법령명 우선
    for n in root.iter():
        if _local(n.tag) in ("법령명_한글", "법령명한글"):
            if n.text and n.text.strip():
                law_name = n.text.strip()
            break

    rows: list[dict] = []
    for n in root.iter():
        if _local(n.tag) != "조문단위":
            continue
        kids = {_local(c.tag): c for c in n}
        kind = (kids["조문여부"].text if kids.get("조문여부") is not None else "") or ""
        if kind.strip() != "조문":
            continue  # 전문(章 제목) 스킵
        no = (kids["조문번호"].text if kids.get("조문번호") is not None else "") or ""
        no = no.strip()
        if not no:
            continue
        branch = (
            kids["조문가지번호"].text if kids.get("조문가지번호") is not None else ""
        ) or ""
        branch = branch.strip()
        title = (kids["조문제목"].text if kids.get("조문제목") is not None else "") or ""
        title = title.strip()
        body = _text_of(kids.get("조문내용"))
        for c in n:
            t = _local(c.tag)
            if t in ("항", "호", "목", "항내용", "호내용"):
                extra = _text_of(c)
                if extra and extra not in body:
                    body = (body + "\n" + extra).strip()
        body = _normalize_body(body)
        art = f"{no}의{branch}" if branch and branch not in ("0", "00") else no
        if len(body) < 8 and not title:
            continue
        rows.append(
            {
                "law_name": law_name,
                "mst": mst,
                "article_no": art,
                "title": title,
                "body": body,
                "kind": "article",
            }
        )
    # 별표·서식
    byeol = _parse_byeol_units(root, law_name, mst)
    rows.extend(byeol)
    return rows


async def resolve_mst(client: httpx.AsyncClient, settings, law_name: str) -> tuple[str, str]:
    """(mst, resolved_name)"""
    url = f"{settings.law_api_base}/DRF/lawSearch.do"
    params = {
        "OC": settings.law_oc,
        "target": "law",
        "type": "XML",
        "query": law_name,
        "display": "10",
    }
    headers = {
        "User-Agent": settings.law_user_agent,
        "Referer": settings.law_referer,
    }
    r = await client.get(url, params=params, headers=headers)
    r.raise_for_status()
    try:
        root = ET.fromstring(r.text)
    except ET.ParseError:
        return "", law_name
    best_mst, best_name = "", law_name
    for node in root.iter():
        tag = _local(node.tag).lower()
        if tag not in ("law", "item"):
            # 법제처: 자식 태그들
            pass
        children = {
            _local(c.tag).lower(): (c.text or "").strip() for c in node
        }
        name = (
            children.get("법령명한글")
            or children.get("lawname")
            or children.get("법령명")
            or ""
        )
        mst = children.get("법령일련번호") or children.get("mst") or ""
        if not name or not mst:
            continue
        # 정식명 완전 일치 우선
        if name == law_name:
            return mst, name
        if law_name in name and not best_mst:
            best_mst, best_name = mst, name
    return best_mst, best_name


async def fetch_full_law(client: httpx.AsyncClient, settings, mst: str) -> str:
    url = f"{settings.law_api_base}/DRF/lawService.do"
    params = {
        "OC": settings.law_oc,
        "target": "law",
        "type": "XML",
        "MST": mst,
    }
    headers = {
        "User-Agent": settings.law_user_agent,
        "Referer": settings.law_referer,
    }
    r = await client.get(url, params=params, headers=headers, timeout=90.0)
    r.raise_for_status()
    return r.text


async def build(laws: list[str] | None = None) -> Path:
    settings = get_settings()
    if not settings.law_oc or settings.use_demo_law:
        raise SystemExit("LAW_OC 필요 (.env). 데모 모드에서는 코퍼스 빌드 불가.")

    names = laws or list(CORE_LAWS.keys())
    OUT.parent.mkdir(parents=True, exist_ok=True)
    all_rows: list[dict] = []

    headers = {
        "User-Agent": settings.law_user_agent,
        "Referer": settings.law_referer,
    }
    async with httpx.AsyncClient(timeout=90.0, follow_redirects=True, headers=headers) as client:
        for name in names:
            print(f"… {name}")
            mst, resolved = await resolve_mst(client, settings, name)
            if not mst:
                print(f"  ! MST 없음, skip")
                continue
            print(f"  mst={mst} name={resolved}")
            xml_text = await fetch_full_law(client, settings, mst)
            rows = parse_law_xml(xml_text, resolved, mst)
            print(f"  articles={len(rows)}")
            all_rows.extend(rows)
            await asyncio.sleep(0.3)  # 예의상 throttle

    # 중복 (law, article) 제거 — 뒤 항목 우선
    seen: set[tuple[str, str]] = set()
    uniq: list[dict] = []
    for r in reversed(all_rows):
        key = (r["law_name"], r["article_no"])
        if key in seen:
            continue
        seen.add(key)
        uniq.append(r)
    uniq.reverse()

    with OUT.open("w", encoding="utf-8") as f:
        for r in uniq:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    print(f"\nWrote {len(uniq)} articles → {OUT}")
    by: dict[str, int] = {}
    for r in uniq:
        by[r["law_name"]] = by.get(r["law_name"], 0) + 1
    for k, v in by.items():
        print(f"  {k}: {v}")
    return OUT


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--law", action="append", help="특정 법령만 (반복 가능)")
    args = ap.parse_args()
    asyncio.run(build(args.law))


if __name__ == "__main__":
    main()
