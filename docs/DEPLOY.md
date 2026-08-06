# SafeLaw 배포

## 무엇이 올라가나

| 포함 | 미포함 |
|:---|:---|
| 앱 코드 + UI | `.env` (시크릿은 호스팅에 직접) |
| 법령 코퍼스 | 이미지 안: `data/kosha/pdfs/` (~490MB) |
| KOSHA 목록 + 검색 청크 | 원문 PDF는 **R2** `qbank-raw/safelaw/kosha/pdfs/` |

원문 PDF: `./scripts/upload_kosha_r2.sh --go` 로 업로드.  
앱은 `/api/kosha/pdf/file/{code}` → R2 **presigned** 리다이렉트.

## 관리(키 저장·PDF 업로드)

배포 URL에서는 **로컬 전용**이라 웹 UI로 키를 못 바꿉니다.  
→ 호스팅 **Environment Variables** 에 키를 넣으세요.

필수/권장:

```
GROQ_API_KEY=...          # 무료 티어 LLM (우선)
GROQ_MODEL=llama-3.1-8b-instant
LAW_OC=...
GEMINI_API_KEY=...        # 선택 (Groq 없을 때)
DATA_GO_KR_KEY=...        # 선택
DEMO_MODE=auto
ENABLE_LLM=true
```

운영 UI(PDF 수집·API 키·상태 배지): **로컬** 또는 `https://…/?admin=1`

## A) Render 무료 (현재 · https://safelaw.onrender.com)

**잠자기:** 약 15분 무접속 시 절전 → 다음 접속 30초~1분 지연.

### 자동 배포 (main push)

서비스에 **Auto-Deploy = Yes**, branch **main**, repo  
`https://github.com/popconcity-ship-it/safelaw` 가 연결돼 있어야 한다.

```bash
# 재확인·재활성화
render services update srv-d9plkdrm8hqs73fqlr20 \
  --auto-deploy --repo https://github.com/popconcity-ship-it/safelaw \
  --branch main --confirm -o text

# 수동 배포 (자동이 안 돌 때)
./scripts/deploy_render.sh
# 또는
render deploys create srv-d9plkdrm8hqs73fqlr20 --commit "$(git rev-parse HEAD)" --confirm --wait
```

Dashboard: https://dashboard.render.com/web/srv-d9plkdrm8hqs73fqlr20  
→ **Settings → Build & Deploy → Auto-Deploy** 가 On 인지 확인.  
GitHub 앱 권한이 빠지면 push 해도 배포가 안 되고 예전에 `api` 트리거만 쌓인다.

### Environment (Dashboard)

```text
GROQ_API_KEY=...
LAW_OC=...
GEMINI_API_KEY=...   # 선택
DEMO_MODE=auto
ENABLE_LLM=true
```

헬스: `https://safelaw.onrender.com/api/health`

### 법령 개정 자동 반영

| 층 | 동작 |
|:---|:---|
| **주간 Actions** | `.github/workflows/refresh-law.yml` — 매주 코퍼스+별표 페이지 인덱스 재생성 → `main` 커밋 → Render 배포 |
| **수동 일괄** | `./scripts/refresh_law_data.sh` (로컬, `LAW_OC` + `pdftotext` 필요) |
| **런타임 보완** | 페이지 인덱스에 없는 `fl_seq` 요청 시 서버가 PDF 받아 즉시 인덱싱 (poppler, 재시작 시 유실 가능) |

GitHub **Settings → Secrets** 에 `LAW_OC` 를 넣어야 주간 워크플로가 동작한다.  
Actions 탭에서 `refresh-law-data` → **Run workflow** 로 즉시 실행 가능.

```bash
# 로컬 수동
./scripts/refresh_law_data.sh
git add data/law/ && git commit -m "chore(law): 법령 코퍼스 갱신" && git push
./scripts/deploy_render.sh   # auto-deploy 꺼져 있을 때
```

### CLI

```bash
brew install render
render login
```

## B) Railway (대안)

1. https://railway.app → GitHub → `safelaw`
2. Variables 에 동일 키
3. 체험 크레딧 후 유료 성향 — 테스트 무료면 Render 우선

## C) 로컬 Docker (검증)

```bash
cd ~/Documents/safelaw
docker build -t safelaw .
docker run --rm -p 8787:8787 --env-file .env safelaw
# http://127.0.0.1:8787
```

## 배포 후 체크

- [ ] `GET /api/health` → law_api / llm 이 기대와 같음
- [ ] 채팅: `지도사` → 산안법 142 등
- [ ] PDF 원문 링크는 포털로 열림 (로컬 파일 없음 — 정상)
