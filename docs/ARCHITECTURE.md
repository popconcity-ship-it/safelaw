# SafeLaw 아키텍처 (MVP)

산업안전·중대재해 실무용 **법규 AI**.  
핵심 가치: **조문을 지어내지 않는다** — 법제처 조회 + 인용 검증 게이트.

## 하이브리드 구조

```
[사용자 질문]
      │
      ▼
┌─────────────────┐
│  Orchestrator   │  의도 분류 + 도구 순서 강제
└────────┬────────┘
         │
    ┌────┴────┐
    ▼         ▼
[실시간 법령]  [도메인 지식]   ← Phase 2
 법제처 API     KOSHA GUIDE
 (또는 demo)    재해사례 RAG
    │         │
    └────┬────┘
         ▼
   [검증 레이어]  verify_citations
         ▼
   [생성 레이어]  답변 / (추후) 위험성평가·TBM
         ▼
   [프론트]  채팅 + 출처 배지
```

## 레이어

### 1. 실시간 법령 (Law API Layer) — **MVP 완료 골격**

| 항목 | 내용 |
|------|------|
| 소스 | 법제처 Open API (`LAW_OC`) |
| 핵심 법령 | 산안법(+영·규칙), 중처법(+영), 산안기준규칙, 위험성평가 지침 |
| 약칭 | `산안법`→산업안전보건법, `중처법`→중대재해 처벌 등에 관한 법률 |
| 캐시 | 검색 1h, 조문 24h |
| 데모 | `LAW_OC` 없으면 `DEMO_ARTICLES` 로컬 corpus |

도구 (코드):

- `LawClient.search_law`
- `LawClient.get_article`
- `LawClient.get_articles_for_query` (키워드·조문 휴리스틱)
- `verify_citations` / `extract_citations`

### 2. 도메인 지식 (Domain RAG) — **Phase 2**

- KOSHA GUIDE, 재해사례, 고용부 해석
- 벡터DB + 메타데이터 (`industry`, `hazard_type`, `related_articles`)
- 법령 레이어와 **분리** — 실무 해설은 RAG, 규범 인용은 법제처만

### 3. Orchestrator

의도 예:

| intent | 동작 |
|--------|------|
| `risk_assessment` | 산안법 36 + 지침 |
| `serious_accident` | 중처법 2·4 |
| `article_lookup` | 명시 조문 조회 |
| `document` | (Phase 2) 템플릿 생성 |
| `general` | 키워드 매핑 후 조회 |

순서 강제: **조문 수집 → 생성 → 인용 검증**.

### 4. 검증 레이어

- 조문 실존
- (선택) 괄호 제목 vs 공식 제목 일치
- 실패 시 답변 하단 경고 + 법적 근거 사용 금지 안내

### 5. 생성 레이어

- OpenAI Chat Completions (키 있으면)
- 없으면 템플릿 `demo_answer` (조문 발췌 + 출처)

## 디렉터리

```
safelaw/
├── backend/app/
│   ├── main.py           # FastAPI 엔트리 + 정적 프론트
│   ├── config.py
│   ├── api/chat.py       # /api/chat, /verify, /law/*
│   ├── law/              # 법제처 클라이언트 + 검증
│   ├── agent/            # Orchestrator + prompts
│   └── models/schemas.py
├── frontend/index.html   # 채팅 MVP UI
└── docs/
```

## API

| Method | Path | 설명 |
|--------|------|------|
| GET | `/` | 채팅 UI |
| GET | `/api/health` | 법제처/LLM/데모 상태 |
| POST | `/api/chat` | `{ message, history?, workplace? }` → 답변+인용 |
| POST | `/api/verify` | 임의 텍스트 인용 검증 |
| GET | `/api/law/search?q=` | 법령 검색 |
| GET | `/api/law/article?law=&article=` | 조문 조회 |

## 로드맵

1. **MVP (현재)** — 법제처/데모 + 채팅 + 인용 검증 + 웹 UI  
2. **문서 생성** — 위험성평가표·TBM 템플릿  
3. **KOSHA RAG** — 가이드·사례 임베딩  
4. **커뮤니티·뉴스** — 안전인형 확장 기능  

## 참고

- [korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp) — 법제처 래핑·`verify_citations` 패턴
- 법제처 Open API: https://open.law.go.kr/
