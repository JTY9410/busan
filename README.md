## 부산 조합 책임 보험 가입 전산

### 개발 스택
- Python 3.11
- Flask + SQLite
- Bootstrap 5 + Tailwind CDN
- Docker
- (옵션) Vercel 배포

### 빠른 시작 (로컬)
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

- 접속: http://localhost:5000

### Docker 실행
```bash
# 로컬 빌드 및 실행
./docker-build.sh

# 또는 직접 실행
docker build -t busan-insurance .
docker run -p 5000:5000 -v $(pwd)/data:/app/data busan-insurance
```

### Docker Hub 업로드
```bash
# Docker Hub에 업로드 (자세한 내용은 DOCKER_UPLOAD.md 참고)
./docker-upload.sh <DOCKERHUB_USERNAME>
```

### Vercel 배포
- `vercel.json`과 `api/index.py`가 포함되어 있습니다.
- Vercel Project 생성 후 Python Runtime로 배포하세요.

**유용한 Vercel 명령어:**
```bash
# 프로덕션 배포
vercel --prod

# 배포 목록 확인
vercel ls

# 최근 배포 로그 확인
vercel logs

# 대시보드 열기 (macOS)
./vercel-open.sh

# 또는 직접 브라우저에서 접속
# https://vercel.com/dashboard
```

### 기능 요약
- 회원가입(관리자 승인 후 로그인 가능)
- 대시보드/약관 페이지
- 책임보험 가입: 검색, 신규등록, 엑셀 업/다운로드
- 관리자: 회원 승인, 책임보험 승인(일괄승인), 데이터 다운로드
- 정산: 월별 상사별 건수/금액, 청구서 개별/일괄 인쇄(브라우저 PDF 저장)

### 로고
- 처음 실행 시 `/Users/USER/dev/busan/스크린샷 2025-10-31 오후 4.40.25.png`를 `static/logo.png`로 복사 시도합니다.
