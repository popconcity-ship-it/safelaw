# KOSHA 데이터 디렉터리

| 경로 | Git | 설명 |
|:---|:---:|:---|
| `guide_catalog.json` | ✅ | 지침 목록 (~1,350건) |
| `guide_list.csv` | ✅ | 원본 목록 백업 |
| `pdfs/` | ❌ | 원문 PDF (~490MB) — 로컬/R2 |
| `text/` | ❌ | PDF 추출 JSON |
| `index/` | ❌ | 검색 청크 (`chunks.jsonl`) |

## 로컬에서 PDF·인덱스 채우기

```bash
# 포털/API로 PDF 수집 (이미 있으면 skip)
python3 scripts/download_kosha_pdfs.py

# 텍스트 추출 + 청크 인덱스
python3 scripts/ingest_kosha_pdfs.py
```

배포 시 PDF는 Git이 아니라 **R2/S3** 경로를 권장합니다.
