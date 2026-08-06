"""법령 위계 해석 — 법률 조 → 시행령·시행규칙·별표 자동 연결.

「고용노동부령으로 정하는 바」「대통령령으로 정하는 바」「별표 N」을
조문 본문·하위 규범 역참조로 풀어 시드한다.
도메인 if 를 늘리지 않고 일반 규칙으로 하위 규범을 붙인다.
"""

from __future__ import annotations

import re
from functools import lru_cache

from .corpus import get_corpus_article, load_corpus, normalize_article_key

# 빈출: 본문 파싱 실패 시 보강 (정본은 하위 규범의 「법 제N조」 역참조)
_KNOWN_CHILDREN: dict[tuple[str, str], list[tuple[str, str]]] = {
    ("산업안전보건법", "29"): [
        ("산업안전보건법 시행규칙", "26"),
        ("산업안전보건법 시행규칙", "27"),
        ("산업안전보건법 시행규칙", "별표4"),
        ("산업안전보건법 시행규칙", "별표5"),
    ],
    ("산업안전보건법", "36"): [
        ("사업장 위험성평가에 관한 지침", "3"),
        ("산업안전보건법 시행규칙", "37의3"),
    ],
    ("산업안전보건법", "114"): [
        ("산업안전보건법 시행규칙", "167"),
        ("산업안전보건법 시행규칙", "169"),
        ("산업안전보건법", "175"),
        ("산업안전보건법 시행령", "별표35"),
    ],
    ("산업안전보건법", "175"): [
        ("산업안전보건법 시행령", "별표35"),
    ],
    ("산업안전보건법", "15"): [
        ("산업안전보건법 시행령", "별표1"),  # 있을 때만
    ],
    ("산업안전보건법", "17"): [
        ("산업안전보건법 시행령", "별표3"),
    ],
    ("에너지이용 합리화법", "39"): [
        ("에너지이용 합리화법", "73"),
        ("열사용기자재의 검사 및 검사면제에 관한 기준", "개요"),
        ("열사용기자재의 검사 및 검사면제에 관한 기준", "3편"),
    ],
}

_BYEOL_IN_BODY = re.compile(r"별표\s*(\d+)(?:\s*의\s*(\d+))?")
_PARENT_REF = re.compile(
    r"법\s*제\s*(\d+)(?:\s*조)?(?:\s*의\s*(\d+))?"
    r"|제\s*(\d+)\s*조(?:\s*의\s*(\d+))?"
)


def _art_key(main: str, sub: str | None = None) -> str:
    if sub:
        return f"{main}의{sub}"
    return main


def _parent_family(law_name: str) -> str:
    """법률 정식명 → 시행령/규칙 접두."""
    n = (law_name or "").strip()
    # 이미 하위면 법률 본체 추정
    for suf in (" 시행규칙", " 시행령"):
        if n.endswith(suf):
            return n[: -len(suf)]
    return n


def _subordinate_laws(parent_law: str) -> list[str]:
    base = _parent_family(parent_law)
    out = [
        f"{base} 시행령",
        f"{base} 시행규칙",
    ]
    if "산업안전보건" in base:
        out.append("산업안전보건기준에 관한 규칙")
        out.append("사업장 위험성평가에 관한 지침")
    if "에너지이용" in base:
        out.append("열사용기자재의 검사 및 검사면제에 관한 기준")
    return out


def _same_family(parent_law: str, child_law: str) -> bool:
    """산안법 자손이 에너지법 시행령에 붙지 않도록 가족 제한."""
    p = parent_law or ""
    c = child_law or ""
    base = _parent_family(p)
    if not c:
        return False
    if c == base or c.startswith(base):
        return True
    if "산업안전보건" in base:
        return "산업안전" in c or "위험성평가" in c
    if "에너지이용" in base:
        return "에너지" in c or "열사용" in c
    if "중대재해" in base:
        return "중대재해" in c
    # 기본: 앞 4글자 공유
    return len(base) >= 4 and base[:4] in c


def extract_byeol_refs(body: str) -> list[str]:
    keys: list[str] = []
    for m in _BYEOL_IN_BODY.finditer(body or ""):
        k = f"별표{m.group(1)}"
        if m.group(2):
            k += f"의{m.group(2)}"
        if k not in keys:
            keys.append(k)
    return keys


def body_wants_ordinance(body: str) -> bool:
    b = body or ""
    return any(
        x in b
        for x in (
            "고용노동부령",
            "기후에너지환경부령",
            "부령으로 정",
            "총리령",
        )
    )


def body_wants_decree(body: str) -> bool:
    b = body or ""
    return "대통령령" in b


@lru_cache(maxsize=1)
def _backref_index() -> dict[str, list[tuple[str, str]]]:
    """「법 제N조」 → [(하위법령, 조/별표), …] 역인덱스."""
    inv: dict[str, list[tuple[str, str]]] = {}
    for row in load_corpus():
        law = row.get("law_name") or ""
        art = str(row.get("article_no") or "")
        # 하위 규범만
        if not any(
            x in law
            for x in ("시행령", "시행규칙", "기준에 관한 규칙", "지침", "검사 및 검사면제")
        ):
            continue
        blob = f"{row.get('title') or ''}\n{(row.get('body') or '')[:800]}"
        for m in re.finditer(r"법\s*제\s*(\d+)(?:\s*조)?(?:\s*의\s*(\d+))?", blob):
            key = _art_key(m.group(1), m.group(2))
            inv.setdefault(key, [])
            pair = (law, art)
            if pair not in inv[key]:
                inv[key].append(pair)
        # 별표 제목의 (제26조제1항 관련)
        for m in re.finditer(r"제\s*(\d+)\s*조", blob[:200]):
            if "관련" in blob[:120] or art.startswith("별표"):
                key = m.group(1)
                inv.setdefault(key, [])
                pair = (law, art)
                if pair not in inv[key]:
                    inv[key].append(pair)
    return inv


def children_for_article(parent_law: str, parent_art: str) -> list[tuple[str, str]]:
    """법률(또는 상위) 조 → 연결할 하위 (법령명, 조번호) 목록."""
    art_k = normalize_article_key(str(parent_art))
    # 숫자만
    m = re.match(r"(\d+)(?:의(\d+))?", art_k)
    if not m:
        # 별표·편 자체면 확장 없음
        return []
    main = m.group(1)
    sub = m.group(2)
    art_norm = _art_key(main, sub)

    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(law: str, art: str) -> None:
        if not _same_family(parent_law, law):
            return
        pair = (law, str(art))
        if pair in seen:
            return
        if not get_corpus_article(law, art):
            return
        seen.add(pair)
        out.append(pair)

    # 0) 알려진 맵
    base = _parent_family(parent_law)
    for pl, pa in ((parent_law, art_norm), (base, art_norm), (base, main)):
        for law, art in _KNOWN_CHILDREN.get((pl, pa), []):
            add(law, art)

    # 1) 본문 별표 직접 지정
    row = get_corpus_article(parent_law, parent_art) or get_corpus_article(base, main)
    body = (row or {}).get("body") or ""
    title = (row or {}).get("title") or ""
    for bk in extract_byeol_refs(body):
        for sub_law in _subordinate_laws(base):
            add(sub_law, bk)
            add(f"{base} 시행령", bk)
            add(f"{base} 시행규칙", bk)

    # 2) 부령·대통령령 → 같은 가족 역참조만
    inv = _backref_index()
    candidates = list(inv.get(art_norm) or []) + list(inv.get(main) or [])
    want_rule = body_wants_ordinance(body) or "교육" in title or "평가" in title
    want_decree = body_wants_decree(body)
    for law, art in candidates:
        if not _same_family(base, law):
            continue
        # 역참조 노이즈: 제목·본문 앞부분에 법 제N조가 있는 것만 우선
        # (인덱스에 이미 포함 — 가족 필터로 충분)
        if want_rule and "시행규칙" in law:
            add(law, art)
        elif want_decree and "시행령" in law:
            add(law, art)
        elif str(art).startswith("별표"):
            add(law, art)
        elif want_rule or want_decree:
            add(law, art)
        else:
            add(law, art)

    return out[:10]


def expand_article_list(
    pairs: list[tuple[str, str]],
    *,
    max_total: int = 10,
) -> list[tuple[str, str]]:
    """(법령, 조) 목록에 위계 자식을 붙인 확장 목록.

    부모 시드를 모두 먼저 넣은 뒤 자식을 붙인다 (자식이 시드를 밀어내지 않음).
    """
    out: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()

    def add(law: str, art: str) -> None:
        key = (law, str(art))
        if key in seen:
            return
        if not get_corpus_article(law, art):
            return
        seen.add(key)
        out.append(key)

    # 1) 입력 쌍 전부
    for law, art in pairs:
        add(law, art)
        if len(out) >= max_total:
            return out

    # 2) 부모마다 자식
    for law, art in list(pairs):
        if len(out) >= max_total:
            break
        if str(art).startswith("별표") or str(art).endswith("편") or art == "개요":
            continue
        for cl, ca in children_for_article(law, art):
            add(cl, ca)
            if len(out) >= max_total:
                break
    return out
