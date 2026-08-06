"""시스템 프롬프트 — 산업안전 법규 AI."""

from __future__ import annotations

import re

SYSTEM_PROMPT = """당신은 산업안전·중대재해 분야 법규 도우미 'SafeLaw'입니다.
한국 산업안전보건법, 중대재해 처벌 등에 관한 법률, 관련 시행령·규칙·고시를 근거로 답합니다.

## 절대 규칙
1. 아래에 제공된 [근거 조문]에 있는 내용만 법적 근거로 인용하세요.
2. 근거 조문에 없는 조항 번호·제목·금액을 지어내지 마세요.
3. 조문 인용 형식 (실제 숫자로, 예시 문자 금지):
   - 좋은 예: [산업안전보건법 제175조], [산업안전보건법 시행령 별표 35], [산업안전보건법 제114조]
   - 항·호까지 쓸 때: [산업안전보건법 제175조제5항제3호] 또는 산안법 제175조제5항제3호
   - ⛔ 금지: 제N조, [법령명 제N조], [법령정식명 제N조], "N조" 같은 자리표시자 문구를 그대로 출력
   - 같은 조문을 문장 뒤·다음 줄에 반복하지 마세요. 인용만 있는 단독 줄 금지.
4. 과태료·금액 질문: 근거에 「구조화 금액」 또는 개별기준 행이 있으면 반드시 숫자로 답하세요.
   - 1차·2차·3차 위반을 구분해 "1차 100만원, 2차 200만원, 3차 500만원"처럼 쓰세요
   - 3차 금액만 단독으로 "500만원입니다"라고 단정하지 마세요 (질문이 1회 위반일 수 있음)
   - 규모 감경·가중이 근거에 있으면 짧게 덧붙이세요
   - 근거에 행이 없을 때만 "개별기준 행을 찾지 못했다"고 하세요
5. 근거 조문 블록에 해당 조가 있으면 "확인할 수 없습니다"라고 하지 마세요.
6. 실무 조언은 "참고"로 구분하세요.
7. [KOSHA 가이드]는 실무 참고이며 법 조항처럼 인용하지 마세요. 긴 KOSHA 발췌 금지.
8. 인사말·자기소개 금지. 바로 본론.
9. 말미 한 줄 면책: 참고용이며 최종 판단은 전문가/관할 기관 확인이 필요하다고 적으세요.

## 답변 형식
- 금액 질문: 첫 문장에 1·2·3차 금액 → 이어서 의무 조문·과태료 근거 조문 링크 형식 인용
- 조문 인용은 문장 안 실제 조번호만 (자리표시자 금지)
- KOSHA 장황 나열 금지
"""


def build_user_prompt(
    question: str,
    articles_block: str,
    workplace: dict | None = None,
    kosha_block: str | None = None,
) -> str:
    parts = [f"## 질문\n{question}"]
    if workplace:
        parts.append(f"## 사업장 정보\n{workplace}")
    parts.append(f"## 근거 조문 (법적 인용은 이 내용만)\n{articles_block}")
    if kosha_block:
        parts.append(
            f"## KOSHA 가이드 시드 (실무 참고, 법 조항 아님)\n{kosha_block}"
        )
    parts.append("근거 조문만 법적으로 인용하고, KOSHA는 실무 참고로 구분해 답변하세요.")
    return "\n\n".join(parts)


def _article_label(a) -> str:
    """조문/별표/고시 편 표시 라벨."""
    art = str(getattr(a, "article_no", "") or "")
    law = getattr(a, "law_name", "") or ""
    if art.startswith("별표"):
        return f"{law} {art.replace('별표', '별표 ', 1)}"
    if art.startswith("별지"):
        return f"{law} 별지 제{art.replace('별지', '', 1)}호서식"
    if art == "개요" or art.endswith("편"):
        return f"{law} {art}"
    return f"{law} 제{art}조"


# 질문 동의어 → 별표 본문 검색어 (미부착 등 구어체)
_BYEOL_QUERY_EXPAND: list[tuple[re.Pattern[str], list[str]]] = [
    (re.compile(r"msds|물질안전|물질\s*안전\s*보건", re.I), ["물질안전보건", "물질안전"]),
    (
        re.compile(r"미부착|미게시|미비치|안\s*붙|안\s*달|게시\s*안|갖추"),
        ["게시하지", "갖추어 두지", "게시", "갖추"],
    ),
    (re.compile(r"미제공|안\s*주|제공\s*안"), ["제공하지", "제공"]),
    (re.compile(r"미제출|제출\s*안"), ["제출하지", "제출"]),
    (re.compile(r"경고\s*표시|경고표지|라벨"), ["경고표시", "경고"]),
    (re.compile(r"과태료"), ["과태료"]),
    (re.compile(r"안전관리자"), ["안전관리자"]),
    (re.compile(r"보건관리자"), ["보건관리자"]),
    (re.compile(r"산업안전보건위원회|산안위"), ["산업안전보건위원회"]),
    (re.compile(r"안전보건교육|교육"), ["교육"]),
    (re.compile(r"위험성\s*평가"), ["위험성평가", "위험성 평가"]),
]


def _byeol_query_terms(question: str) -> list[str]:
    """질문 → 별표 행 검색용 키워드 (확장 포함)."""
    q = (question or "").strip()
    terms: list[str] = []
    seen: set[str] = set()
    for pat, expand in _BYEOL_QUERY_EXPAND:
        if pat.search(q):
            for t in expand:
                if t not in seen:
                    seen.add(t)
                    terms.append(t)
    stop = {
        "얼마", "금액", "인가요", "해주세요", "알려", "알려줘", "뭐야", "얼마야",
        "경우", "위반", "관련", "기준", "부과", "내용", "확인", "대한", "해서",
        "하는", "있는", "없는", "인가요", "입니까",
    }
    for m in re.finditer(r"[가-힣A-Za-z0-9]{2,}", q):
        tok = m.group(0)
        if tok in stop or tok.lower() in stop:
            continue
        if tok not in seen:
            seen.add(tok)
            terms.append(tok)
    return terms[:16]


def _normalize_byeol_text(body: str) -> str:
    """박스문자 표 → 검색 가능한 평문."""
    t = body or ""
    t = re.sub(r"[┌┐└┘├┤┬┴┼─│┃━┏┓┗┛┣┫┳┻╋]+", " ", t)
    t = re.sub(r"[|｜]{2,}", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    return t


def _repair_byeol_cites(text: str) -> str:
    """표 줄바꿈으로 깨진 '제175조제5 항제3호' → '제175조제5항제3호'.

    금액 행을 삼키지 않도록 짧은 공백·짧은 비숫자 간격만 연결.
    """
    t = text or ""
    t = re.sub(
        r"제\s*(\d+)\s*조\s*제?\s*(\d+)\s*항\s*제?\s*(\d+(?:의\d+)?)\s*호",
        r"제\1조제\2항제\3호",
        t,
    )
    t = re.sub(
        r"제(\d+)조제(\d+)\s{0,4}항제(\d+(?:의\d+)?)호?",
        r"제\1조제\2항제\3호",
        t,
    )
    # 제175조제5 + (비숫자 짧은 조각) + 항제3호
    t = re.sub(
        r"제(\d+)조제(\d+)(?:\s+[^\d]{0,12}?)항제(\d+(?:의\d+)?)호",
        r"제\1조제\2항제\3호",
        t,
    )
    t = re.sub(r"법\s*제\s*(\d+)\s*조", r"법 제\1조", t)
    return t


def _structure_fine_chunk(chunk: str) -> str:
    """평문 발췌 → LLM이 읽기 쉬운 구조화 금액 블록."""
    raw = re.sub(r"\s{2,}", " ", chunk or "").strip()
    if not raw:
        return ""

    # 금액은 원문에서 먼저 (cite 복구가 1) 행을 망가뜨릴 수 있음)
    items: list[str] = []
    for m in re.finditer(
        r"(\d+)\)\s*([^0-9]{6,160}?)\s*(\d{1,4})\s+(\d{1,4})\s+(\d{1,4})",
        raw,
    ):
        desc = re.sub(r"\s+", " ", m.group(2)).strip(" ·,")
        # 게시/갖추/제공/제출 등 의미 토큰이 설명에 없으면 원문 근처 보강
        if not any(k in desc for k in ("게시", "갖추", "제공", "제출", "경고", "선임", "작성")):
            # 뒤 문맥 조금 포함
            tail = raw[m.end() : m.end() + 40]
            desc = (desc + " " + re.sub(r"\s+", " ", tail)).strip()[:90]
        items.append(
            f"  {m.group(1)}) {desc} → "
            f"1차 {m.group(3)}만원 / 2차 {m.group(4)}만원 / 3차 {m.group(5)}만원"
        )
        if len(items) >= 5:
            break

    c = _repair_byeol_cites(raw)

    # 표 칸 분리로 떨어진 항·호 힌트 수집 (제175조제5 … 항제3호)
    for m in re.finditer(
        r"제(\d+)조제(\d+)\b.{0,120}?항제(\d+(?:의\d+)?)호",
        raw,
    ):
        fixed = f"제{m.group(1)}조제{m.group(2)}항제{m.group(3)}호"
        if fixed not in c:
            c = c + " " + fixed

    duties = re.findall(
        r"법\s*제\d+조(?:제\d+항)?(?:제\d+(?:의\d+)?호)?(?:부터\s*제\d+조(?:제\d+항)?(?:까지)?)?",
        c,
    )
    bases = re.findall(r"제\d+조제\d+항제\d+(?:의\d+)?호", c)
    duty_u: list[str] = []
    base_u: list[str] = []
    for d in duties:
        if "175" in d:
            base_u.append(d if d.startswith("법") else "법 " + d)
        else:
            duty_u.append(d)
    for b in bases:
        base_u.append("법 " + b if not b.startswith("법") else b)

    def uniq(xs: list[str]) -> list[str]:
        out: list[str] = []
        for x in xs:
            if x not in out:
                out.append(x)
        return out

    duty_u, base_u = uniq(duty_u)[:3], uniq(base_u)[:4]

    if not items:
        for m in re.finditer(r"(\d{1,4})\s+(\d{1,4})\s+(\d{1,4})", raw):
            a, b, d = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if a > 3000 or b > 3000:
                continue
            items.append(
                f"  (표기) 1차 {a}만원 / 2차 {b}만원 / 3차 {d}만원"
            )
            if len(items) >= 3:
                break

    lines = ["【구조화 금액 — 단위 만원, 지어내지 말고 아래만 인용】"]
    if duty_u:
        lines.append("의무 조문: " + ", ".join(duty_u))
    if base_u:
        lines.append("과태료 근거: " + ", ".join(base_u))
    if items:
        lines.append("개별 금액:")
        lines.extend(items)
    else:
        lines.append("개별 금액: (숫자 파싱 실패 — 원문 참고)")
    lines.append("원문 발췌: " + raw[:380])
    return "\n".join(lines)


def _extract_relevant_byeol_rows(
    body: str, question: str, max_chars: int = 1400
) -> str:
    """질문 키워드가 있는 별표 개별기준 → 구조화 금액 발췌."""
    terms = _byeol_query_terms(question)
    if not terms or not body:
        return ""
    flat = _normalize_byeol_text(body)
    m4 = re.search(r"4\.\s*개별기준", flat)
    area = flat[m4.start() :] if m4 else flat

    core = [
        t
        for t in terms
        if t
        in {
            "물질안전보건",
            "물질안전",
            "게시하지",
            "갖추어 두지",
            "경고표시",
            "안전관리자",
            "보건관리자",
            "산업안전보건위원회",
            "위험성평가",
            "위험성 평가",
            "제출하지",
            "제공하지",
        }
    ]
    if not core:
        core = terms[:4]

    positions: list[int] = []
    for term in terms:
        start = 0
        n_term = 0
        while n_term < 4:
            i = area.find(term, start)
            if i < 0:
                break
            positions.append(i)
            start = i + max(len(term), 1)
            n_term += 1

    if not positions:
        return ""

    need_msds = any(t in terms for t in ("물질안전보건", "물질안전"))
    need_post = any(t in terms for t in ("게시하지", "갖추어 두지", "게시", "갖추"))

    def window_score(pos: int) -> int:
        w = area[max(0, pos - 120) : pos + 400]
        if need_msds and ("물질안전" not in w):
            return -1
        if need_msds and need_post and not any(
            x in w for x in ("게시", "갖추", "제공", "제출", "경고")
        ):
            return -1
        s = 0
        for t in core:
            if t in w:
                s += 3
        for t in terms:
            if t in w:
                s += 1
        if re.search(r"\d{1,4}\s+\d{1,4}\s+\d{1,4}", w):
            s += 2
        return s

    ranked = sorted(set(positions), key=lambda p: (-window_score(p), p))
    raw_chunks: list[str] = []
    used: list[int] = []
    for pos in ranked:
        if window_score(pos) < 3:
            continue
        if any(abs(pos - u0) < 220 for u0 in used):
            continue
        a = max(0, pos - 180)
        b = min(len(area), pos + 520)
        chunk = area[a:b]
        head = re.search(
            r"(?:[가-힣]{1,3}\.|[0-9]+\)|[가나다라마바사아자차카타파하]\))\s*법\s*제",
            chunk,
        )
        if head and head.start() < 120:
            chunk = chunk[head.start() :]
        chunk = re.sub(r"\s{2,}", " ", chunk).strip()
        if len(chunk) < 40:
            continue
        used.append(pos)
        raw_chunks.append(chunk)
        if len(raw_chunks) >= 2:
            break

    if not raw_chunks:
        pos = ranked[0]
        a, b = max(0, pos - 180), min(len(area), pos + 520)
        chunk = re.sub(r"\s{2,}", " ", area[a:b]).strip()
        if chunk:
            raw_chunks = [chunk]

    # 미부착/미게시 질문: 제출·수입 행 제외 (게시·갖추 행만)
    if need_msds and need_post:
        posted = [
            ch
            for ch in raw_chunks
            if any(k in ch for k in ("게시", "갖추"))
            and "제출하지" not in ch[:80]
        ]
        if posted:
            raw_chunks = posted[:1]

    if not raw_chunks:
        return ""

    structured = [_structure_fine_chunk(ch) for ch in raw_chunks]
    structured = [s for s in structured if s]
    out = "\n\n".join(structured)
    if len(out) > max_chars:
        out = out[:max_chars].rstrip() + "…"
    return (
        out
        + "\n\n(※ 금액은 별표 표의 만원 단위. 1회 위반이면 보통 1차 금액. "
        "규모 감경·가중 가능. 답변에 1·2·3차를 모두 쓰고, 자리표시자(제N조) 금지. "
        "의무 조문·과태료 근거는 [산업안전보건법 제114조] [산업안전보건법 제175조제5항제3호] "
        "처럼 실제 번호로 인용.)"
    )


def _byeol_general_head(body: str, max_len: int = 500) -> str:
    """별표 일반기준(1~3) 요약 — 개별기준 표 이전."""
    t = (body or "").strip()
    if not t:
        return ""
    cut = re.search(
        r"(?:^|\n)\s*(?:4\.\s*개별기준|[┌┐└┘├┤┬┴┼─│]|\|{3,})",
        t,
    )
    if cut and cut.start() > 80:
        t = t[: cut.start()].strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > max_len:
        t = _clean_excerpt(t, max_len)
    return t


def _byeol_llm_excerpt(body: str, question: str = "", max_len: int = 700) -> str:
    """별표 → 일반기준 요약 + (질문 관련 시) 개별 금액 행.

    전문 3만 자는 넣지 않음. 질문 키워드로 행만 뽑아 금액 답변 가능하게.
    """
    t = (body or "").strip()
    if not t:
        return "(본문 없음 — UI 별표 PDF 확인)"

    head = _byeol_general_head(t, max_len=min(max_len, 480))
    rows = _extract_relevant_byeol_rows(t, question, max_chars=1100) if question else ""

    if rows:
        parts = []
        if head:
            parts.append(head)
        parts.append(rows)
        return "\n\n".join(parts)

    # 관련 행 없음: 일반기준만 + 안내
    if not head:
        head = _clean_excerpt(t, max_len)
    return (
        head
        + "\n\n(※ 질문과 직접 맞는 개별기준 행을 자동 발췌하지 못함. "
        "금액을 지어내지 말고, 확인 불가·별표 PDF/카드 확인을 안내하세요.)"
    )


def _article_llm_body(a, question: str = "") -> str:
    """LLM용 조문/별표 본문 — UI용 전문과 분리 (토큰 절감)."""
    art = str(getattr(a, "article_no", "") or "")
    body = getattr(a, "body", "") or ""
    if art.startswith("별표") or art.startswith("별지"):
        return _byeol_llm_excerpt(body, question=question, max_len=700)
    # 일반 조문: 항 2개 분량 정도
    return _law_excerpt(body, max_hang=2) or _clean_excerpt(body, 600)


def format_articles_block(articles: list, question: str = "") -> str:
    """LLM 프롬프트용. UI cards 의 전문과 달리 발췌만 넣음.

    별표 3만 자 표 전문을 넣으면 Groq 한도·타임아웃·비용이 폭증함.
    질문 관련 별표 행(금액)만 골라 붙임.
    """
    if not articles:
        return "(검색된 조문 없음 — 법적 주장을 하지 말고 확인 불가 안내)"
    blocks = []
    total = 0
    budget = 5200  # 개별 금액 행 포함 여유
    for a in articles:
        label = _article_label(a)
        body = _article_llm_body(a, question=question)
        chunk = f"### [{label}] {getattr(a, 'title', '') or ''}\n{body}"
        if total + len(chunk) > budget and blocks:
            blocks.append(
                f"### (이하 생략) 추가 조문/별표는 UI 카드·PDF 참고 — "
                f"남은 {len(articles) - len(blocks)}건"
            )
            break
        blocks.append(chunk)
        total += len(chunk)
    return "\n\n".join(blocks)


def _clean_excerpt(text: str, max_len: int = 320) -> str:
    """PDF 추출 찌꺼기 정리 + 문장 단위로 자르기."""
    t = text or ""
    t = re.sub(r"\(PDF[^)]*\)\s*", "", t)
    t = re.sub(r"KOSHA\s*GUIDE\s*[A-Z]?\s*[-–]?\s*\d+\s*[-–]?\s*\d+", "", t, flags=re.I)
    t = re.sub(r"KOSHAGUIDE[A-Z0-9\-]*", "", t, flags=re.I)
    t = re.sub(r"-\s*\d+\s*-", " ", t)
    t = re.sub(r"[ \t]+", " ", t)
    t = re.sub(r"\n{2,}", "\n", t)
    t = re.sub(r"([가-힣])\n+([가-힣])", r"\1\2", t)
    t = t.strip()
    if len(t) <= max_len:
        return t
    cut = t[: max_len + 80]  # 여유 두고 문장 끝 탐색
    # 한국어 문장 종결 우선 (…한다. / 다. / .)
    best = -1
    for m in re.finditer(r"[다요음임]\.|다\.|요\.|임\.|음\.|[.。]\s", cut):
        if m.end() <= max_len + 40:
            best = m.end()
    if best > max_len // 3:
        return cut[:best].rstrip()
    # 항 구분(②③) 앞에서 끊기
    for m in re.finditer(r"[②③④⑤⑥⑦⑧⑨⑩]", cut):
        if max_len // 3 < m.start() <= max_len + 40:
            return cut[: m.start()].rstrip()
    return cut[:max_len].rstrip() + "…"


def _law_excerpt(body: str, max_hang: int = 2) -> str:
    """조문 본문 — ① 항을 끝까지, 가능하면 ②까지. 문장 중간 절단 금지."""
    body = (body or "").strip()
    if not body:
        return ""
    # 조문 제목 줄 제거 여지
    body = re.sub(r"^제\d+조[^\n]*\n?", "", body).strip() or body

    # ①②③… 로 항 분리
    parts = re.split(r"(?=①|②|③|④|⑤|⑥|⑦|⑧|⑨|⑩)", body)
    parts = [p.strip() for p in parts if p.strip()]
    if not parts:
        return _clean_excerpt(body, 600)

    selected = parts[:max_hang]
    text = "\n".join(selected)
    # 그래도 너무 길면 마지막 항만 문장 단위 축소
    if len(text) > 900:
        text = _clean_excerpt(text, 900)
    return text


def _guide_title(hit) -> str:
    title = (getattr(hit, "title", None) or "").strip()
    code = (getattr(hit, "code", None) or "").strip()
    if not title or title == code or re.fullmatch(r"[A-Z]-\d+-\d+", title or ""):
        # catalog 제목 보강
        try:
            from ..kosha.catalog import load_catalog

            for g in load_catalog():
                if g.get("code") == code and g.get("title"):
                    return g["title"]
        except Exception:
            pass
        return title or code
    return title


def demo_answer(question: str, articles: list, kosha_hits: list | None = None) -> str:
    """LLM 없거나 실패 시 — 짧고 읽기 쉬운 검색 요약 답변."""
    kosha_hits = list(kosha_hits or [])
    # 시드/관련도 낮은 항목은 답변 본문에서 제외 (PDF·관련 catalog만)
    display_kosha = []
    for k in kosha_hits:
        src = getattr(k, "source", "seed")
        if src == "seed":
            continue  # 본문에는 PDF 위주, 시드는 생략
        if src == "catalog" and len(display_kosha) >= 1:
            continue
        display_kosha.append(k)
        if len(display_kosha) >= 2:
            break
    # PDF가 하나도 없으면 시드 1개라도
    if not display_kosha:
        display_kosha = kosha_hits[:2]

    has_law = bool(articles)
    has_kosha = bool(display_kosha)

    if not has_law and not has_kosha:
        q = (question or "").strip()
        if len(q) < 2:
            return (
                "질문이 너무 짧습니다. **두 글자 이상**으로 물어 주세요.\n"
                "예: `지도사`, `밀폐공간`, `위험성평가`\n\n"
                "※ 참고용 · 최종 판단은 전문가·관할 기관 확인"
            )
        return (
            f"「{q}」에 바로 맞는 조문·KOSHA 가이드를 찾지 못했습니다.\n"
            "조금 더 구체적으로 물어 보시면 좋습니다. "
            "예: `산업안전지도사 자격`, `밀폐공간 작업`\n\n"
            "※ 참고용 · 최종 판단은 전문가·관할 기관 확인"
        )

    lines: list[str] = []

    # —— 결론 (짧게) ——
    bullets: list[str] = []
    if any(k in question for k in ("밀폐", "질식")):
        bullets = [
            "밀폐공간은 **출입금지 표지·목록 관리** 후, 불가피할 때만 **출입허가**",
            "작업 전·중 **산소·유해가스 측정**, **환기**, **감시인·구조 체계** 필수",
            "무허가·단독 출입 금지 · 상세는 아래 KOSHA GUIDE 참조",
        ]
    elif any(k in question for k in ("위험성평가", "위험성 평가")):
        if any(
            k in question
            for k in ("의무", "해야", "해당", "50인", "5인", "미만", "있나요", "인가")
        ):
            bullets = [
                "**예.** 상시근로자 수와 관계없이 사업주는 [산안법 제36조]에 따른 "
                "**위험성평가 의무**가 있습니다.",
                "규모·업종은 평가 **방법·주기** 등에 영향을 줄 수 있습니다. "
                "조문 번호([산안법 제36조])를 누르면 전문을 볼 수 있습니다.",
                "근로자 참여 · 결과 기록·보존",
            ]
        else:
            bullets = [
                "사업주는 유해·위험 요인을 찾아 **위험성평가** 후 개선 조치 ([산안법 제36조])",
                "근로자 참여 · 결과 기록·보존 · 조문 번호를 누르면 전문 확인",
            ]
    elif any(k in question for k in ("중대재해", "중처법", "경영책임")):
        bullets = [
            "경영책임자등은 안전보건관리체계 구축·이행 등 의무 ([중처법 제4조])",
        ]
    elif any(
        k in question
        for k in ("산업안전지도사", "산업보건지도사", "지도사")
    ):
        bullets = [
            "산안법상 **산업안전지도사·산업보건지도사**는 평가·지도·계획서 작성 등 직무 ([산안법 제142조])",
            "자격시험 합격 → (필요 시 연수교육) → **등록** 후 직무 수행 ([산안법 제143·145·146조])",
            "※ 중소기업 **경영·기술지도사** 법률과는 별개 제도",
        ]
    elif has_kosha:
        titles = " · ".join(
            f"{getattr(k, 'code', '')}" for k in display_kosha[:2]
        )
        bullets = [f"관련 KOSHA GUIDE: **{titles}** (아래 발췌)", "법령 근거는 하단에 요약"]
    else:
        bullets = ["검색된 법령 조문을 기준으로 안내합니다."]

    lines.append("### 결론")
    for b in bullets:
        lines.append(f"- {b}")
    lines.append("")

    # 조문 전문은 프론트 펼침 카드(articles_used) — 본문 중복 출력 안 함

    # —— KOSHA (카드 힌트 — 본문 발췌는 짧게) ——
    if has_kosha:
        lines.append("### KOSHA GUIDE")
        for k in display_kosha:
            code = getattr(k, "code", "") or ""
            title = _guide_title(k)
            src = getattr(k, "source", "seed")
            badge = "PDF" if src == "pdf" else ("목록" if src == "catalog" else "요약")
            raw = (getattr(k, "body", None) or getattr(k, "summary", "") or "")
            page_m = re.search(r"p\.(\d+)", raw)
            page = page_m.group(1) if page_m else ""
            excerpt = _clean_excerpt(raw, 220)
            lines.append(f"**[{code}]** {title} `{badge}`" + (f" · p.{page}" if page else ""))
            if excerpt:
                lines.append(f"> {excerpt}")
            url = getattr(k, "url", None) or ""
            if url.startswith("/api/kosha/pdf/file/"):
                lines.append(f"[📄 원문 PDF 보기]({url})")
            elif code:
                lines.append(f"[📄 원문 PDF 보기](/api/kosha/pdf/file/{code})")
            if url.startswith("http"):
                lines.append(f"[산업안전포털]({url})")
            lines.append("")

    lines.append("---")
    lines.append(
        "_검색 기반 요약(LLM 미사용 또는 실패 시). 참고용 · "
        "최종은 법령·KOSHA 원문 및 전문가 확인._"
    )
    return "\n".join(lines)
