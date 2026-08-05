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
GEMINI_API_KEY=...
LAW_OC=...
DATA_GO_KR_KEY=...   # 선택
GEMINI_MODEL=gemini-2.0-flash
DEMO_MODE=auto
```

## A) Render 무료 (현재 권장 · 테스트용)

**잠자기:** 약 15분 무접속 시 서버 절전 → 다음 접속 시 30초~1분 지연 (테스트 감수).

### 클릭 배포 (Blueprint)

1. https://dashboard.render.com 가입 (GitHub 로그인)
2. **New** → **Blueprint**
3. 저장소 **`popconcity-ship-it/safelaw`** 연결 (Private이면 Render에 GitHub 권한 허용)
4. `render.yaml` 감지 → **Apply**
5. 서비스 **Environment** 에 키 입력 후 저장 (Redeploy):

```text
GEMINI_API_KEY=...
LAW_OC=...
DATA_GO_KR_KEY=...   # 선택
GEMINI_MODEL=gemini-2.0-flash
DEMO_MODE=auto
```

6. 배포 완료 후 URL: `https://safelaw-xxxx.onrender.com`
7. 확인: `https://…/api/health`

원클릭 시도 (로그인 필요):

https://dashboard.render.com/blueprint/new?repo=https%3A%2F%2Fgithub.com%2Fpopconcity-ship-it%2Fsafelaw

### CLI (선택)

```bash
brew install render   # 또는 Render 문서의 CLI
render login
# 대시보드 Blueprint가 더 단순함
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
