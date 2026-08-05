"""위험성평가표 · TBM 일지 생성."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Literal

from ..kosha.search import KoshaHit, format_kosha_block, search_kosha
from ..models.schemas import Article
from .templates import RISK_ASSESSMENT_SKELETON, TBM_SKELETON

DocType = Literal["risk_assessment", "tbm"]


@dataclass
class DocumentResult:
    doc_type: DocType
    title: str
    markdown: str
    articles: list[Article] = field(default_factory=list)
    kosha: list[KoshaHit] = field(default_factory=list)
    used_llm: bool = False

    def to_dict(self) -> dict:
        return {
            "doc_type": self.doc_type,
            "title": self.title,
            "markdown": self.markdown,
            "articles": [a.model_dump() for a in self.articles],
            "kosha": [k.to_dict() for k in self.kosha],
            "used_llm": self.used_llm,
        }


def detect_doc_type(text: str) -> DocType | None:
    t = text.lower()
    if any(k in text for k in ("TBM", "tbm", "안전점검회의", "툴박스", "작업 전 회의", "작업전 회의")):
        return "tbm"
    if any(k in text for k in ("위험성평가표", "위험성 평가표", "위험성평가", "위험성 평가")):
        if any(k in text for k in ("만들어", "작성", "생성", "양식", "표", "일지", "초안")):
            return "risk_assessment"
        if "평가" in text and any(k in text for k in ("작성", "만들어", "생성")):
            return "risk_assessment"
    if "일지" in text and any(k in t for k in ("tbm", "안전")):
        return "tbm"
    return None


def _guess_work(text: str, workplace: dict[str, Any] | None) -> str:
    if workplace and workplace.get("work"):
        return str(workplace["work"])
    # 따옴표 안 작업명
    m = re.search(r"[\"'「]([^\"'」]{2,40})[\"'」]", text)
    if m:
        return m.group(1)
    for key in ("지게차", "용접", "도장", "굴착", "전기", "밀폐", "고소", "하역", "청소", "사무실"):
        if key in text:
            return f"{key} 작업"
    return "일반 작업"


def _guess_industry(text: str, workplace: dict[str, Any] | None) -> str:
    if workplace and workplace.get("industry"):
        return str(workplace["industry"])
    for key in ("제조", "건설", "물류", "화학", "사무실", "조선", "플랜트"):
        if key in text:
            return key
    return "공통"


def _legal_refs(articles: list[Article]) -> str:
    if not articles:
        return "- (조문 미확보 — 산안법 제36조 등 원문 확인)"
    lines = []
    for a in articles:
        title = f" ({a.title})" if a.title else ""
        lines.append(f"- [{a.law_name} 제{a.article_no}조]{title}")
    return "\n".join(lines)


def _kosha_refs(hits: list[KoshaHit]) -> str:
    if not hits:
        return "- (관련 가이드 시드 없음)"
    return "\n".join(f"- [{h.code}] {h.title}" for h in hits)


def _template_risk(
    work: str,
    industry: str,
    workplace_name: str,
    articles: list[Article],
    kosha: list[KoshaHit],
) -> str:
    # 작업별 기본 위험 행
    presets: dict[str, list[tuple[str, str, str, str]]] = {
        "지게차": [
            ("보행자 충돌", "중", "통로 분리·경보·속도제한", "현장관리자 / 1주"),
            ("화물 낙하·전도", "고", "과적금지·적재기준·스팟터", "운전자·관리감독자 / 즉시"),
            ("무자격 운전", "고", "자격 확인·시동키 관리", "관리감독자 / 즉시"),
        ],
        "고소": [
            ("추락", "고", "난간·안전대·개구부 덮개", "작업지휘자 / 즉시"),
            ("공구 낙하", "중", "공구 끈·출입통제", "작업자 / 당일"),
        ],
        "밀폐": [
            ("질식·중독", "고", "측정·환기·출입허가·감시인", "허가권자 / 작업 전"),
        ],
        "화학": [
            ("흡입·누출", "고", "MSDS·환기·보호구·흡수제", "취급책임자 / 즉시"),
        ],
        "사무실": [
            ("화재·피난 장애", "중", "소화기·비상구·피난도", "총무 / 1주"),
            ("전기 과부하", "중", "멀티탭 정리·점검", "관리자 / 2주"),
            ("미끄러짐", "저", "바닥 물기 제거·매트", "담당 / 상시"),
        ],
    }
    rows = None
    for k, v in presets.items():
        if k in work:
            rows = v
            break
    if not rows:
        rows = [
            ("협착·충돌", "중", "방호장치·동선 정리·표지", "관리감독자 / 1주"),
            ("미끄러짐·전도", "중", "정리정돈·조명·논슬립", "담당 / 상시"),
            ("근골격 부담", "저", "중량물 보조·작업자세 교육", "관리자 / 2주"),
        ]
        # KOSHA 힌트 반영
        for h in kosha[:2]:
            for ht in h.hazard_types[:2]:
                if ht not in ("종합", "교육", "TBM"):
                    rows.append((ht, "중", f"{h.title} 참고 조치", "담당 / 협의"))

    haz_lines = [
        "| 번호 | 유해·위험요인 | 현재 상태 | 가능성 | 중대성 | 위험성 |",
        "|------|----------------|-----------|--------|--------|--------|",
    ]
    ctrl_lines = [
        "| 번호 | 감소대책 | 잔여위험 | 담당 | 기한 | 완료 |",
        "|------|----------|----------|------|------|------|",
    ]
    for i, (haz, level, ctrl, who) in enumerate(rows, 1):
        haz_lines.append(f"| {i} | {haz} | 현장 확인 필요 | 중 | {level} | {level} |")
        ctrl_lines.append(f"| {i} | {ctrl} | 저 | {who} | ☐ |")

    return RISK_ASSESSMENT_SKELETON.format(
        workplace_name=workplace_name,
        industry=industry,
        work=work,
        date=date.today().isoformat(),
        hazards_table="\n".join(haz_lines),
        controls_table="\n".join(ctrl_lines),
        legal_refs=_legal_refs(articles),
        kosha_refs=_kosha_refs(kosha),
    )


def _template_tbm(
    work: str,
    industry: str,
    location: str,
    articles: list[Article],
    kosha: list[KoshaHit],
) -> str:
    haz = ["□ 미끄러짐·전도", "□ 협착·충돌", "□ 전기", "□ 화재"]
    ctrl = ["□ 정리정돈", "□ 보호구 착용", "□ 비상구·소화기 위치 확인", "□ 이상 시 작업 중단·보고"]
    ppe = ["□ 안전화", "□ 안전모(해당 시)", "□ 장갑", "□ 기타: ________"]
    if "지게차" in work:
        haz = ["□ 보행자 충돌", "□ 화물 낙하", "□ 전도", "□ 무자격 운전"]
        ctrl = ["□ 전용통로·서행", "□ 경광·경보 확인", "□ 과적 금지", "□ 스팟터 배치(해당 시)"]
        ppe = ["□ 안전화", "□ 안전조끼", "□ 안전벨트(좌석)", "□ 기타"]
    if "고소" in work or "추락" in work:
        haz = ["□ 추락", "□ 개구부", "□ 낙하물", "□ 악천후"]
        ctrl = ["□ 난간·덮개", "□ 안전대 체결", "□ 공구 낙하 방지", "□ 작업 중지 기준"]
    if "밀폐" in work:
        haz = ["□ 산소결핍", "□ 유해가스", "□ 질식"]
        ctrl = ["□ 출입허가", "□ 가스·산소 측정", "□ 환기", "□ 감시인"]
    # kosha body first line hints
    extra = []
    for h in kosha[:1]:
        extra.append(f"(가이드 참고: {h.title})")

    return TBM_SKELETON.format(
        datetime=datetime.now().strftime("%Y-%m-%d %H:%M"),
        location=location,
        work=work,
        industry=industry,
        work_detail=f"- {work}\n- 세부 내용: (현장에서 기입)\n" + "\n".join(extra),
        hazards="\n".join(haz),
        controls="\n".join(ctrl),
        ppe="\n".join(ppe),
        emergency="- 즉시 작업 중단 → 관리자 보고 → 필요 시 119/내부 비상연락\n- 집합 장소: (기입)",
        notes="- 질문·애로사항: \n- 기타: ",
        legal_refs=_legal_refs(articles),
        kosha_refs=_kosha_refs(kosha),
    )


async def generate_document(
    *,
    doc_type: DocType,
    query: str,
    articles: list[Article],
    workplace: dict[str, Any] | None = None,
    llm_fill: str | None = None,
) -> DocumentResult:
    work = _guess_work(query, workplace)
    industry = _guess_industry(query, workplace)
    workplace_name = (workplace or {}).get("name") or (workplace or {}).get("workplace_name") or "(사업장명)"
    location = (workplace or {}).get("location") or "작업 현장"

    kosha = search_kosha(f"{query} {work} {industry}", industry=industry, limit=3)

    if doc_type == "tbm":
        title = f"TBM 일지 초안 — {work}"
        base = _template_tbm(work, industry, str(location), articles, kosha)
    else:
        title = f"위험성평가표 초안 — {work}"
        base = _template_risk(work, industry, str(workplace_name), articles, kosha)

    md = base
    used_llm = False
    if llm_fill and llm_fill.strip():
        # LLM이 채운 본문이 있으면 템플릿 뒤에 보완 섹션으로 붙이거나 대체
        # 구조 유지를 위해 템플릿 + LLM 보완
        md = base + "\n\n---\n## AI 보완 내용\n" + llm_fill.strip()
        used_llm = True

    return DocumentResult(
        doc_type=doc_type,
        title=title,
        markdown=md,
        articles=articles,
        kosha=kosha,
        used_llm=used_llm,
    )


def document_llm_prompt(
    doc_type: DocType,
    query: str,
    articles_block: str,
    kosha_block: str,
    workplace: dict[str, Any] | None,
) -> str:
    kind = "TBM(작업 전 안전점검회의) 일지" if doc_type == "tbm" else "위험성평가표"
    return f"""당신은 산업안전 실무 문서 작성 보조입니다.
요청: {kind} 초안의 **내용 보완** (표 구조는 이미 있음).

## 사용자 요청
{query}

## 사업장
{workplace or "(미입력)"}

## 근거 조문 (법적 인용은 여기만)
{articles_block}

## KOSHA 실무 가이드 시드 (참고, 법 조항처럼 인용하지 말 것)
{kosha_block}

## 작성 규칙
1. 마크다운 불릿으로 위험요인 3~6개, 감소대책, 보호구, 비상조치를 구체적으로.
2. 법령 조항 번호는 근거 조문에 있을 때만.
3. 없는 사실을 단정하지 말고 (현장 확인) 표시.
4. 인사말 금지. 본문만.
"""
