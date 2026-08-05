# SafeLaw — 산업안전 특화 법규 AI

[안전인(anjeonin)](https://safety.cleanmission.co.kr/anjeonin) 같은 **법규 AI**의 MVP 골격.

> **조문을 지어내지 않는다** — 법제처(또는 로컬 데모 corpus)에서 조문을 가져온 뒤 답하고,  
> 답변 속 인용을 다시 검증합니다.

## 배포

📄 **[`docs/DEPLOY.md`](docs/DEPLOY.md)** — Railway / Render / Docker  
원문 PDF는 이미지에 넣지 않습니다. 시크릿은 호스팅 환경변수로 넣습니다.

## 빠른 시작 (데모 모드, API 키 불필요)

```bash
cd safelaw/backend
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8787
```

브라우저: **http://127.0.0.1:8787/**

데모 모드에서는 산안법·중처법 일부 조문이 로컬에 내장되어 있어  
바로 채팅·인용 검증 흐름을 확인할 수 있습니다.

## 라이브 연동

```bash
cp .env.example .env
# .env 편집:
#   GEMINI_API_KEY=...   ← Google AI Studio 키
#   LAW_OC=...           ← 법제처 (선택, 없으면 데모 조문)
```

| 변수 | 설명 |
|------|------|
| `GROQ_API_KEY` | [Groq Console](https://console.groq.com/keys) — **무료 티어 LLM (우선)** |
| `GROQ_MODEL` | 기본 `llama-3.3-70b-versatile` |
| `GEMINI_API_KEY` | [Google AI Studio](https://aistudio.google.com/apikey) — Groq 없을 때 |
| `GEMINI_MODEL` | 기본 `gemini-2.0-flash` |
| `ENABLE_LLM` | `true` 면 LLM 사용, `false` 면 검색 요약만 |
| `LAW_OC` | [법제처 Open API](https://open.law.go.kr/LSO/openApi/guideList.do) 인증키 |
| `OPENAI_API_KEY` | (선택) 추가 폴백 |
| `DEMO_MODE` | `auto`(기본) / `true` / `false` |
| `ADMIN_TOKEN` | (선택) 원격 관리용. **보통 비움** |

키 파일 위치: **`~/Documents/safelaw/.env`**

### 관리 기능 (쉬운 규칙)

- **API 키 저장 · PDF 업로드** → 서버가 켜진 **이 컴퓨터(로컬)** 에서만
- **채팅 · 법령 · PDF 보기** → 그대로 가능
- 배포 후 인터넷 사용자는 관리 불가 (키는 서버 `.env`를 직접 수정)
- 토큰 입력 없음. 평소에 신경 쓸 것 없음

## 구조

```
safelaw/
├── backend/app/     # FastAPI — 법령 클라이언트, 검증, Orchestrator
├── frontend/        # 채팅 UI (서버가 / 로 서빙)
└── docs/ARCHITECTURE.md
```

상세 설계: [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md)

## API 예시

```bash
# 헬스
curl -s http://127.0.0.1:8787/api/health | python3 -m json.tool

# 채팅
curl -s -X POST http://127.0.0.1:8787/api/chat \
  -H 'Content-Type: application/json' \
  -d '{"message":"50인 미만도 위험성평가 해야 하나요?"}' | python3 -m json.tool

# 인용 검증
curl -s -X POST http://127.0.0.1:8787/api/verify \
  -H 'Content-Type: application/json' \
  -d '{"text":"산업안전보건법 제36조에 따라 위험성평가를 실시한다."}'
```

## MVP 범위

- [x] 산업안전 핵심 법령 약칭·검색·조문 조회
- [x] 채팅 + 근거 조문 주입 프롬프트
- [x] 인용 검증 게이트
- [x] 키 없이 돌아가는 데모 모드
- [x] 웹 채팅 UI · API 키 설정 UI
- [x] KOSHA 가이드 시드 검색 (키워드, 12종 요약 corpus)
- [x] KOSHA GUIDE 목록 1,352건 검색
- [x] PDF 본문 추출·청킹·검색·자동 인용 파이프라인
- [x] 위험성평가표 / TBM 문서 초안 생성 · 복사·md 다운로드
- [ ] 공식 KOSHA PDF 1,352건 일괄 수집 (포털 인증/다운로드 제한)
- [ ] 대화 기록 저장 (DB)
- [ ] 커뮤니티 · 뉴스

### KOSHA PDF 수동·반자동 수집

1. 앱 상단 **「PDF 수집」** 클릭  
2. 우선 목록에서 **포털** 링크로 이동 → 로그인 후 PDF 다운로드  
3. 받은 파일을 업로드(자동 인제스트)  
   또는 `data/kosha/pdfs/M-185-2015.pdf` 로 저장 후 **폴더 전체 인제스트**

```bash
# 우선순위 체크리스트 HTML
python3 scripts/kosha_priority_checklist.py 50
open data/kosha/priority_checklist.html

# 포털 API로 우선순위 PDF 자동 다운로드 + 인제스트
python3 scripts/download_kosha_pdfs.py --limit 40

# 폴더 일괄 인제스트
python3 scripts/ingest_kosha_pdfs.py
```

API: `GET /api/kosha/pdf/priority` · `POST /api/kosha/pdf/upload` · `POST /api/kosha/pdf/ingest`

## 면책

본 서비스(및 데모 조문)는 **참고용**입니다.  
법적 효력이 필요한 판단은 [국가법령정보센터](https://www.law.go.kr) 원문과 전문가·관할 기관에 확인하세요.

## 라이선스 / 참고

- 코드: 프로젝트 내 신규 구현 (MIT 예정 가능)
- 법령 데이터: 법제처 Open API 이용약관 준수
- 설계 참고: [korean-law-mcp](https://github.com/chrisryugj/korean-law-mcp) (MIT)
