"""질문 도메인 라우팅 — 법령 스코프·시드 조문·검색 정책을 일반화.

보일러 if / 산안법 if 를 늘리지 않고, 도메인 테이블로 흡수한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass(frozen=True)
class Domain:
    """검색·답변에 쓰는 도메인 정책."""

    id: str
    # 법령명에 포함되면 허용 (비어 있으면 전체 허용, deny만 적용)
    law_allow: tuple[str, ...] = ()
    # 법령명에 포함되면 제외
    law_deny: tuple[str, ...] = ()
    # 우선 주입 (법령명, 조/별표/편 키)
    seed_articles: tuple[tuple[str, str], ...] = ()
    # 처벌·의무 질문에서 KOSHA 스킵
    skip_kosha: bool = False
    # 형사 벌칙(징역·벌금) 우선 — 과태료 1·2·3차 표 억제
    prefer_criminal: bool = False
    # 별지 서식 제외
    exclude_forms: bool = False
    # 과태료 별표(별표 N) 본문 금액 발췌 억제
    suppress_fine_table: bool = False
    # 근거 조문만으로 답 가능하면 LLM 생략
    grounded_only: bool = False


# 매칭 순서: 구체 → 일반. 첫 히트 채택.
_DOMAIN_RULES: list[tuple[tuple[str, ...], Domain]] = [
    (
        ("물질안전", "msds", "MSDS", "미부착", "미게시", "미비치"),
        Domain(
            id="msds",
            law_allow=("산업안전보건",),
            law_deny=("에너지이용", "열사용기자재"),
            seed_articles=(
                ("산업안전보건법", "114"),
                ("산업안전보건법", "175"),
                ("산업안전보건법 시행령", "별표35"),
            ),
            skip_kosha=True,
            prefer_criminal=False,
            exclude_forms=True,
        ),
    ),
    (
        ("중대재해", "중처법", "경영책임", "중대산업재해"),
        Domain(
            id="serious_accident",
            law_allow=("중대재해",),
            seed_articles=(
                ("중대재해 처벌 등에 관한 법률", "2"),
                ("중대재해 처벌 등에 관한 법률", "4"),
            ),
            skip_kosha=True,
            exclude_forms=True,
        ),
    ),
    (
        ("위험성평가", "위험성 평가"),
        Domain(
            id="risk_assessment",
            law_allow=("산업안전보건", "위험성평가"),
            seed_articles=(
                ("산업안전보건법", "36"),
                ("사업장 위험성평가에 관한 지침", "3"),
            ),
            exclude_forms=True,
        ),
    ),
    # 근로자 안전보건교육 — 법 29 + 시행규칙(고용노동부령) 26·별표4·5
    # ⛔ "해야" 토큰이 시행령 70·71(방호·대여 기계) 제목과 오매칭되지 않게 스코프 고정
    (
        (
            "안전보건교육",
            "안전교육",
            "보건교육",
            "근로자 교육",
            "정기교육",
            "채용 시 교육",
            "채용시 교육",
            "특별교육",
            "작업내용 변경",
            "교육시간",
            "교육 언제",
        ),
        Domain(
            id="safety_education",
            law_allow=("산업안전보건",),
            law_deny=("에너지이용", "열사용기자재", "중대재해"),
            seed_articles=(
                ("산업안전보건법", "29"),
                ("산업안전보건법 시행규칙", "26"),
                ("산업안전보건법 시행규칙", "별표4"),
                ("산업안전보건법 시행규칙", "별표5"),
                ("산업안전보건법 시행규칙", "27"),
            ),
            skip_kosha=True,
            exclude_forms=True,
            grounded_only=True,
        ),
    ),
    (
        ("산업안전지도사", "산업보건지도사", "지도사"),
        Domain(
            id="advisor",
            law_allow=("산업안전보건",),
            seed_articles=(
                ("산업안전보건법", "142"),
                ("산업안전보건법", "143"),
                ("산업안전보건법", "145"),
            ),
            skip_kosha=True,
            exclude_forms=True,
        ),
    ),
    (
        (
            "보일러",
            "열사용",
            "검사대상기기",
            "열매유",
            "압력용기",
            "용접검사",
            "구조검사",
            "검사면제",
            "철금속가열로",
            "설치검사",
            "계속사용검사",
        ),
        Domain(
            id="energy_thermal",
            law_allow=("에너지이용", "열사용기자재"),
            law_deny=("산업안전보건", "중대재해"),
            seed_articles=(
                ("에너지이용 합리화법", "39"),
                ("에너지이용 합리화법", "73"),
                ("열사용기자재의 검사 및 검사면제에 관한 기준", "개요"),
                ("열사용기자재의 검사 및 검사면제에 관한 기준", "2편"),
            ),
            skip_kosha=True,
            prefer_criminal=True,  # 미수검 → 제73조 벌칙 (과태료 표 아님)
            exclude_forms=True,
            suppress_fine_table=True,
            grounded_only=True,  # 벌칙 조문이 명확하면 LLM 생략
        ),
    ),
]

# 열사용 고시 「기준·설치검사」 질문 — 형사 시드 대신 고시 편 우선
_ENERGY_STANDARDS = Domain(
    id="energy_thermal",
    law_allow=("에너지이용", "열사용기자재"),
    law_deny=("산업안전보건", "중대재해"),
    seed_articles=(
        ("열사용기자재의 검사 및 검사면제에 관한 기준", "3편"),  # 설치검사
        ("열사용기자재의 검사 및 검사면제에 관한 기준", "2편"),  # 용접·구조
        ("열사용기자재의 검사 및 검사면제에 관한 기준", "개요"),
        ("에너지이용 합리화법", "39"),
    ),
    skip_kosha=True,
    prefer_criminal=False,
    exclude_forms=True,
    suppress_fine_table=True,
    grounded_only=False,  # 고시 발췌 + LLM/게이트
)

_GENERAL = Domain(id="general")

_PENALTY_KEYS = (
    "처벌",
    "벌칙",
    "과태료",
    "얼마",
    "부과",
    "안받",
    "미검사",
    "미수검",
    "미실시",
)

_STANDARDS_KEYS = (
    "기준",
    "설치검사",
    "설치 검사",
    "용접검사",
    "구조검사",
    "계속사용",
    "시공",
    "천정",
    "천장",
    "이격",
    "거리",
    "옥내",
    "면제",
)


def route_domain(query: str) -> Domain:
    """질문 → 도메인 정책. 매칭 없으면 general."""
    q = (query or "").strip()
    if not q:
        return _GENERAL
    q_l = q.lower()
    for keys, dom in _DOMAIN_RULES:
        for k in keys:
            if k.lower() in q_l or k in q:
                # 에너지: 처벌 vs 고시 기준 질문 분기
                if dom.id == "energy_thermal":
                    is_penalty = any(p in q for p in _PENALTY_KEYS)
                    is_std = any(s in q for s in _STANDARDS_KEYS)
                    if is_std and not is_penalty:
                        return _ENERGY_STANDARDS
                    if is_penalty:
                        return dom  # criminal seeds
                    # 단순 "보일러" 등 — 기준 쪽으로 (고시 검색 우선)
                    if not is_penalty:
                        return _ENERGY_STANDARDS
                return dom
    return _GENERAL


def is_legal_focus(query: str, domain: Domain | None = None) -> bool:
    """법조·처벌 중심 — KOSHA 생략 여부."""
    d = domain or route_domain(query)
    if d.skip_kosha:
        return True
    q = query or ""
    return any(k in q for k in _PENALTY_KEYS) and not any(
        k in q for k in ("KOSHA", "kosha", "가이드", "기술지침")
    )


def law_allowed(law_name: str, domain: Domain) -> bool:
    """도메인 정책상 이 법령을 쓸 수 있는지."""
    law = law_name or ""
    if domain.law_deny and any(d in law for d in domain.law_deny):
        return False
    if domain.law_allow and not any(a in law for a in domain.law_allow):
        return False
    return True


def filter_articles_by_domain(articles: list, domain: Domain) -> list:
    """카드/근거용 조문 목록 필터 + 시드는 client에서 주입."""
    out = []
    for a in articles:
        law = getattr(a, "law_name", None) or (a.get("law_name") if isinstance(a, dict) else "") or ""
        art = str(
            getattr(a, "article_no", None)
            or (a.get("article_no") if isinstance(a, dict) else "")
            or ""
        )
        title = getattr(a, "title", None) or (a.get("title") if isinstance(a, dict) else "") or ""
        if not law_allowed(law, domain):
            continue
        if domain.exclude_forms and (art.startswith("별지") or "서식" in title):
            continue
        if domain.suppress_fine_table and art.startswith("별표"):
            # 에너지 기준 질문: 별표·서식성 표 노이즈 제거
            continue
        if domain.prefer_criminal and art.startswith("별표"):
            body = getattr(a, "body", None) or (a.get("body") if isinstance(a, dict) else "") or ""
            if "과태료" in title or "과태료" in body[:80]:
                continue
        # 고시 기준 질문(비형사): 벌칙 조는 카드 슬롯을 차지하지 않음
        if (
            domain.id == "energy_thermal"
            and not domain.prefer_criminal
            and (art in ("73", "75") or title.strip() == "벌칙")
        ):
            continue
        # 교육 도메인: 방호·대여 기계(70·71·별표20·21) 등 오매칭 제외
        if domain.id == "safety_education":
            if art in ("70", "71") and "시행령" in law:
                continue
            if art in ("별표20", "별표21") and "시행령" in law:
                continue
            if "방호" in title or "대여자" in title:
                continue
            # 교육과 무관한 시행규칙 조
            if "시행규칙" in law and art not in (
                "26",
                "27",
                "28",
                "29",
                "30",
                "별표4",
                "별표5",
            ):
                # 법률 29·30·31·32 는 허용, 규칙 중 교육 외 제외
                if not art.startswith("별표"):
                    body = getattr(a, "body", None) or ""
                    if "교육" not in title and "교육" not in body[:60]:
                        continue
        out.append(a)
    return out


def domain_prompt_hint(domain: Domain) -> str:
    """LLM 시스템/유저 보강 한 줄 (있을 때만)."""
    if domain.id == "energy_thermal":
        return (
            "이 질문은 에너지이용 합리화법·열사용기자재 고시 영역입니다. "
            "검사 미수검은 제39조 의무·제73조 벌칙(징역·벌금)입니다. "
            "산안법 과태료 1·2·3차 형식을 쓰지 마세요."
        )
    if domain.id == "msds":
        return (
            "이 질문은 산업안전보건법 물질안전보건자료(MSDS) 영역입니다. "
            "제114조·제175조·시행령 별표35 과태료를 근거로 하세요."
        )
    if domain.id == "safety_education":
        return (
            "근로자 안전보건교육 질문입니다. "
            "법 제29조의 「고용노동부령」= 산업안전보건법 시행규칙 "
            "(교육시간·내용은 시행규칙 제26조·별표4·별표5). "
            "시행령 제70·71조(방호·대여 기계)와 섞지 마세요."
        )
    if domain.prefer_criminal:
        return "형사 벌칙(징역·벌금) 조문을 우선하고, 과태료 차수 표와 섞지 마세요."
    return ""
