"""시스템 프롬프트 — 산업안전 법규 AI."""

from __future__ import annotations

import re

SYSTEM_PROMPT = """당신은 산업안전·중대재해 분야 법규 도우미 'SafeLaw'입니다.
한국 산업안전보건법, 중대재해 처벌 등에 관한 법률, 관련 시행령·규칙·고시를 근거로 답합니다.

## 절대 규칙
1. 아래에 제공된 [근거 조문]에 있는 내용만 법적 근거로 인용하세요.
2. 근거 조문에 없는 조항 번호·제목을 지어내지 마세요.
3. 조문 인용은 문장 안에만 `[법령정식명 제N조]` 로 한 번 넣으세요.
   - 같은 조문을 문장 뒤·다음 줄에 다시 쓰지 마세요.
   - 인용만 있는 단독 줄 금지. (UI가 문장 안 링크·하단 카드로 보여 줍니다)
4. 근거가 부족하면 "해당 조항을 확인할 수 없습니다"라고 말하고, 확인이 필요한 부분을 명시하세요.
5. 실무 조언은 "참고"로 구분하고, 법적 의무와 혼동되지 않게 쓰세요.
6. [KOSHA 가이드]는 실무 참고이며 법 조항처럼 인용하지 마세요.
   본문 발췌·PDF 링크는 UI가 따로 붙이므로, 답변 본문에는 지침번호만
   한 줄로 언급하거나 생략하세요. (긴 KOSHA 발췌 금지)
7. 인사말·자기소개는 하지 마세요. 바로 본론으로.
8. 답변 말미에 한 줄 면책: 참고용이며 최종 판단은 전문가/관할 기관 확인이 필요하다고 적으세요.

## 답변 형식
- 결론 1~3문장 (질문에 yes/no가 있으면 먼저)
- 관련 조문은 문장 속 `[법령명 제N조]` 한 번만 (중복·단독 줄 금지)
- KOSHA 장황한 나열·페이지 발췌 금지 (UI 카드로 표시됨)
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
    """조문/별표 표시 라벨."""
    art = str(getattr(a, "article_no", "") or "")
    law = getattr(a, "law_name", "") or ""
    if art.startswith("별표"):
        return f"{law} {art.replace('별표', '별표 ', 1)}"
    if art.startswith("별지"):
        return f"{law} 별지 제{art.replace('별지', '', 1)}호서식"
    return f"{law} 제{art}조"


def _byeol_llm_excerpt(body: str, max_len: int = 700) -> str:
    """별표 전문(수만 자 표)을 LLM에 넣지 않고 앞 설명·구조만.

    UI 카드/PDF에 전문이 있으므로, 모델에는 존재·제목·일반기준 요지만.
    """
    t = (body or "").strip()
    if not t:
        return "(본문 없음 — UI 별표 PDF 확인)"
    # 박스 표·개별기준 표 이전까지만
    cut = re.search(
        r"(?:^|\n)\s*(?:4\.\s*개별기준|[┌┐└┘├┤┬┴┼─│]|\|{3,})",
        t,
    )
    if cut and cut.start() > 80:
        t = t[: cut.start()].strip()
    t = re.sub(r"\n{3,}", "\n\n", t)
    if len(t) > max_len:
        t = _clean_excerpt(t, max_len)
    return (
        t
        + "\n\n(※ 개별 위반행위별 과태료 금액 표 전문은 UI 별표 PDF에 있음. "
        "금액 표를 지어내지 말고, 별표 PDF·카드를 보라고 안내하세요.)"
    )


def _article_llm_body(a) -> str:
    """LLM용 조문/별표 본문 — UI용 전문과 분리 (토큰 절감)."""
    art = str(getattr(a, "article_no", "") or "")
    body = getattr(a, "body", "") or ""
    if art.startswith("별표") or art.startswith("별지"):
        return _byeol_llm_excerpt(body, 700)
    # 일반 조문: 항 2개 분량 정도
    return _law_excerpt(body, max_hang=2) or _clean_excerpt(body, 600)


def format_articles_block(articles: list) -> str:
    """LLM 프롬프트용. UI cards 의 전문과 달리 발췌만 넣음.

    별표 3만 자 표 전문을 넣으면 Groq 한도·타임아웃·비용이 폭증함.
    """
    if not articles:
        return "(검색된 조문 없음 — 법적 주장을 하지 말고 확인 불가 안내)"
    blocks = []
    total = 0
    budget = 4500  # 대략 전체 근거 블록 상한 (문자)
    for a in articles:
        label = _article_label(a)
        body = _article_llm_body(a)
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
