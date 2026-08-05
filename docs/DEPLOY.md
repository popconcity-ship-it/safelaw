# SafeLaw 배포

## 무엇이 올라가나

| 포함 | 미포함 |
|:---|:---|
| 앱 코드 + UI | `.env` (시크릿은 호스팅에 직접) |
| 법령 코퍼스 | `data/kosha/pdfs/` (~490MB) |
| KOSHA 목록 + 검색 청크 | 원문 PDF (포털 링크로 대체) |

원문 PDF는 나중에 R2 등에 두고 URL만 붙이면 됩니다.

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

## A) Railway (추천 · GitHub 연동)

1. https://railway.app 로그인 (GitHub)
2. **New Project** → **Deploy from GitHub repo** → `safelaw`
3. Variables 에 위 키 추가
4. 생성되는 `*.up.railway.app` URL 접속
5. `/api/health` 가 `{"status":"ok",...}` 이면 성공

CLI (선택):

```bash
npm i -g @railway/cli
railway login
cd ~/Documents/safelaw
railway init
railway up
railway variables set GEMINI_API_KEY=... LAW_OC=...
```

## B) Render

1. https://dashboard.render.com → **New** → **Blueprint**
2. 이 저장소 연결 (`render.yaml` 사용)
3. Environment 에 키 입력 후 Deploy

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
