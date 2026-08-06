"""법제처 Open API 클라이언트 (산업안전 특화 축소판).

참고: korean-law-mcp 패턴 — Referer/UA 주입, 약칭 확장, 조문번호 변환.
정본 데이터는 법제처. LAW_OC 없으면 demo corpus 사용.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from typing import Any

import httpx
from cachetools import TTLCache

from ..config import Settings, get_settings
from ..models.schemas import Article, LawSearchHit
from .corpus import (
    article_from_row,
    get_corpus_article,
    normalize_article_key,
    search_corpus,
)
from .safety_laws import (
    demo_get_article,
    demo_search,
    match_topic_articles,
    resolve_query_aliases,
)

logger = logging.getLogger(__name__)


def article_no_to_jo(article_no: str) -> str:
    """'36' / '제36조' / '36의2' → 법제처 jo 파라미터 (6자리)."""
    s = article_no.strip()
    s = re.sub(r"^제\s*", "", s)
    s = re.sub(r"조.*$", "", s)
    s = s.strip()
    m = re.match(r"(\d+)(?:의(\d+))?", s)
    if not m:
        digits = re.sub(r"\D", "", s) or "0"
        return digits.zfill(4) + "00"
    main = m.group(1).zfill(4)
    sub = (m.group(2) or "0").zfill(2)
    return main + sub


def jo_to_article_label(jo: str) -> str:
    jo = jo.zfill(6)
    main = int(jo[:4])
    sub = int(jo[4:6])
    if sub:
        return f"{main}의{sub}"
    return str(main)


class LawClient:
    def __init__(self, settings: Settings | None = None):
        self.settings = settings or get_settings()
        self._search_cache: TTLCache = TTLCache(
            maxsize=256, ttl=self.settings.cache_search_ttl
        )
        self._article_cache: TTLCache = TTLCache(
            maxsize=512, ttl=self.settings.cache_article_ttl
        )

    def _headers(self) -> dict[str, str]:
        return {
            "User-Agent": self.settings.law_user_agent,
            "Referer": self.settings.law_referer,
            "Accept": "application/xml, text/xml, */*",
        }

    @property
    def demo(self) -> bool:
        return self.settings.use_demo_law

    async def search_law(self, query: str, display: int = 20) -> list[LawSearchHit]:
        q = resolve_query_aliases(query)
        cache_key = f"search:{q}:{display}"
        if cache_key in self._search_cache:
            return self._search_cache[cache_key]

        if self.demo:
            hits = [
                LawSearchHit(
                    law_name=h["law_name"],
                    mst=h.get("mst"),
                    status=h.get("status"),
                )
                for h in demo_search(q)
            ]
            self._search_cache[cache_key] = hits
            return hits

        url = f"{self.settings.law_api_base}/DRF/lawSearch.do"
        params = {
            "OC": self.settings.law_oc,
            "target": "law",
            "type": "XML",
            "query": q,
            "display": str(display),
        }
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url, params=params, headers=self._headers())
                r.raise_for_status()
                hits = self._parse_search_xml(r.text)
        except Exception as e:
            logger.warning("law search failed: %s", e)
            hits = []

        self._search_cache[cache_key] = hits
        return hits

    def _parse_search_xml(self, xml_text: str) -> list[LawSearchHit]:
        hits: list[LawSearchHit] = []
        if not xml_text.strip() or "<html" in xml_text[:200].lower():
            return hits
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return hits

        # 법제처 응답: LawSearch / law 등 구조 변동 대비
        for node in root.iter():
            tag = node.tag.split("}")[-1].lower()
            if tag not in ("law", "item", "result"):
                continue
            children = {c.tag.split("}")[-1].lower(): (c.text or "").strip() for c in node}
            name = (
                children.get("법령명한글")
                or children.get("lawname")
                or children.get("법령명")
                or children.get("이름")
            )
            if not name:
                continue
            hits.append(
                LawSearchHit(
                    law_name=name,
                    mst=children.get("법령일련번호") or children.get("mst") or children.get("id"),
                    law_id=children.get("법령id") or children.get("lawid"),
                    promulgation_date=children.get("공포일자") or children.get("ancymd"),
                    enforcement_date=children.get("시행일자") or children.get("efymd"),
                    status=children.get("현행연혁코드") or children.get("curhistcd"),
                )
            )
        # 중복 제거
        seen: set[str] = set()
        uniq: list[LawSearchHit] = []
        for h in hits:
            if h.law_name in seen:
                continue
            seen.add(h.law_name)
            uniq.append(h)
        return uniq

    async def get_article(
        self,
        law_name: str,
        article_no: str,
        mst: str | None = None,
    ) -> Article | None:
        art_key = re.sub(r"[^\d의]", "", article_no) or article_no
        # normalize "36의2" etc
        m = re.search(r"(\d+)(?:의(\d+))?", str(article_no))
        if m:
            art_key = m.group(1) + (f"의{m.group(2)}" if m.group(2) else "")

        cache_key = f"art:{law_name}:{art_key}:{mst or ''}"
        if cache_key in self._article_cache:
            return self._article_cache[cache_key]

        if self.demo:
            demo = demo_get_article(law_name, art_key.split("의")[0] if "의" not in art_key else art_key)
            # try plain number
            if not demo:
                num = re.search(r"\d+", art_key)
                if num:
                    demo = demo_get_article(law_name, num.group(0))
            if not demo:
                return None
            article = Article(
                law_name=law_name,
                article_no=art_key,
                title=demo.get("title", ""),
                body=demo.get("body", ""),
                mst=mst,
                source="demo",
            )
            self._article_cache[cache_key] = article
            return article

        # mst 없으면 검색으로 확보
        if not mst:
            hits = await self.search_law(law_name, display=10)
            for h in hits:
                if law_name in h.law_name or h.law_name in law_name:
                    mst = h.mst
                    law_name = h.law_name
                    break
            if not mst and hits:
                mst = hits[0].mst
                law_name = hits[0].law_name

        if not mst:
            return None

        jo = article_no_to_jo(art_key)
        url = f"{self.settings.law_api_base}/DRF/lawService.do"
        params = {
            "OC": self.settings.law_oc,
            "target": "law",
            "type": "XML",
            "MST": mst,
            "JO": jo,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
                r = await client.get(url, params=params, headers=self._headers())
                r.raise_for_status()
                article = self._parse_article_xml(r.text, law_name, art_key, mst)
        except Exception as e:
            logger.warning("get_article failed: %s", e)
            return None

        if article:
            self._article_cache[cache_key] = article
        return article

    def _parse_article_xml(
        self, xml_text: str, law_name: str, article_no: str, mst: str
    ) -> Article | None:
        if not xml_text.strip() or "<html" in xml_text[:200].lower():
            return None
        try:
            root = ET.fromstring(xml_text)
        except ET.ParseError:
            return None

        title = ""
        body_parts: list[str] = []

        for node in root.iter():
            tag = node.tag.split("}")[-1]
            text = (node.text or "").strip()
            if not text:
                continue
            if tag in ("조문여부",):
                continue
            if "제목" in tag or tag in ("조문제목",):
                title = text
            elif any(k in tag for k in ("조문내용", "항내용", "호내용", "목내용", "본문")):
                body_parts.append(text)
            elif tag in ("조문번호",) and not article_no:
                article_no = re.sub(r"[^\d의]", "", text) or article_no

        # fallback: collect significant text nodes
        if not body_parts:
            for node in root.iter():
                t = (node.text or "").strip()
                if len(t) > 20:
                    body_parts.append(t)

        body = "\n".join(body_parts).strip()
        if not body and not title:
            return None

        return Article(
            law_name=law_name,
            article_no=article_no,
            title=title,
            body=body or title,
            mst=mst,
            source="law_api",
        )

    async def get_articles_for_query(self, query: str, limit: int = 4) -> list[Article]:
        """질문에서 관련 조문 수집.

        우선순위:
        0) 도메인 시드 조문
        1) 명시 「제N조」·별표
        2) 주제 키워드 맵
        3) 로컬 코퍼스 전문검색 (도메인 스코프)
        """
        from .domain_router import (
            filter_articles_by_domain,
            law_allowed,
            route_domain,
        )

        expanded = resolve_query_aliases(query)
        domain = route_domain(expanded)
        articles: list[Article] = []

        def _append(a: Article | None) -> None:
            if not a:
                return
            if not law_allowed(a.law_name or "", domain):
                return
            if domain.exclude_forms and (
                str(a.article_no).startswith("별지") or "서식" in (a.title or "")
            ):
                return
            if any(
                x.law_name == a.law_name and x.article_no == a.article_no for x in articles
            ):
                return
            articles.append(a)

        from .hierarchy import expand_article_list

        # 0) 도메인 시드 (+ 위계 자식)
        seed_pairs = expand_article_list(list(domain.seed_articles), max_total=limit + 4)
        for law, art in seed_pairs:
            cached = get_corpus_article(law, art)
            if cached:
                _append(article_from_row(cached))
            if len(articles) >= limit:
                return filter_articles_by_domain(articles, domain)[:limit]

        # 1) 명시 「별표 N」 — 코퍼스 별표 행
        for m in re.finditer(
            r"(?:([가-힣A-Za-zㆍ·\s]{2,40}?)\s*)?별표\s*(\d+)(?:\s*의\s*(\d+))?",
            expanded,
        ):
            law_hint = (m.group(1) or "").strip()
            art = f"별표{m.group(2)}" + (f"의{m.group(3)}" if m.group(3) else "")
            candidates = []
            if law_hint:
                candidates.append(law_hint)
            if domain.law_allow:
                # 도메인 법령 우선
                if any("산업안전" in a for a in domain.law_allow):
                    candidates.extend(
                        ["산업안전보건법 시행령", "산업안전보건법 시행규칙", "산업안전보건법"]
                    )
                if any("중대재해" in a for a in domain.law_allow):
                    candidates.append("중대재해 처벌 등에 관한 법률 시행령")
                if any("에너지" in a for a in domain.law_allow):
                    candidates.extend(
                        ["에너지이용 합리화법 시행령", "에너지이용 합리화법 시행규칙"]
                    )
            candidates.extend(
                [
                    "산업안전보건법 시행령",
                    "산업안전보건법 시행규칙",
                    "중대재해 처벌 등에 관한 법률 시행령",
                    "산업안전보건법",
                ]
            )
            for law_name in candidates:
                if not law_allowed(law_name, domain):
                    continue
                hit = get_corpus_article(law_name, art)
                if hit:
                    _append(article_from_row(hit))
                    break
            if len(articles) >= limit:
                return filter_articles_by_domain(articles, domain)[:limit]

        # 2) 명시적 "제N조" 패턴 — 코퍼스 우선 (네트워크 0)
        explicit = re.findall(
            r"([가-힣A-Za-zㆍ·\s]{2,40}?)\s*제\s*(\d+)(?:\s*조|\s*의\s*(\d+))?",
            expanded,
        )
        for law_hint, main, sub in explicit[:limit]:
            law_hint = law_hint.strip()
            art = f"{main}의{sub}" if sub else main
            law_name = law_hint if len(law_hint) >= 2 else "산업안전보건법"
            if not law_allowed(law_name, domain) and domain.law_allow:
                # 도메인 밖 법령 힌트는 스킵
                continue
            cached = get_corpus_article(law_name, art)
            if not cached:
                for hit in search_corpus(f"{law_name} 제{art}조", limit=5, domain=domain):
                    if str(hit.get("article_no")) == str(art):
                        if not law_hint or law_name in (hit.get("law_name") or "") or (
                            hit.get("law_name") or ""
                        ) in law_name:
                            cached = hit
                            break
            if cached:
                _append(article_from_row(cached))
            else:
                if len(law_hint) < 2:
                    hits = await self.search_law(expanded, display=5)
                    law_name = hits[0].law_name if hits else "산업안전보건법"
                if law_allowed(law_name, domain):
                    _append(await self.get_article(law_name, art))

        # 3) 주제 맵 (도메인 허용 법령만) + 위계 확장
        topic_pairs = match_topic_articles(expanded)
        topic_pairs = expand_article_list(topic_pairs, max_total=limit + 6)
        for law, art in topic_pairs:
            if not law_allowed(law, domain):
                continue
            cached = get_corpus_article(law, art)
            if not cached:
                for hit in search_corpus(f"{law} {art}", limit=4, domain=domain):
                    ha = str(hit.get("article_no") or "")
                    if ha == str(art) or normalize_article_key(ha) == normalize_article_key(
                        str(art)
                    ):
                        cached = hit
                        break
            if cached:
                _append(article_from_row(cached))
            if len(articles) >= limit:
                break

        # 3-b) 이미 모은 법률 조 → 하위 규범 한 번 더
        if len(articles) < limit:
            parents = [
                (a.law_name, str(a.article_no))
                for a in articles
                if a.law_name
                and not str(a.article_no).startswith("별표")
                and "시행" not in (a.law_name or "")
            ]
            for law, art in expand_article_list(parents, max_total=8):
                if not law_allowed(law, domain):
                    continue
                cached = get_corpus_article(law, art)
                if cached:
                    _append(article_from_row(cached))
                if len(articles) >= limit:
                    break

        # 4) 코퍼스 전문검색 보강
        if len(articles) < limit:
            corpus_hits = search_corpus(expanded, limit=max(limit * 2, 8), domain=domain)
            for hit in corpus_hits:
                _append(article_from_row(hit))
                if len(articles) >= limit:
                    break

        # 5) 위계 확장 후 필터 (법률 조가 있으면 무관 별표 제거는 도메인 필터)
        return filter_articles_by_domain(articles, domain)[:limit]
