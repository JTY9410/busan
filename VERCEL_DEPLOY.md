# Vercel 배포 가이드

## 배포 전 확인 사항

1. **환경 변수 설정 (Vercel 대시보드)**
   - Vercel 프로젝트 설정 > Environment Variables에서 다음 변수들을 설정하세요:
     - `SECRET_KEY`: 강력한 비밀번호 (예: openssl rand -hex 32로 생성)
     - `VERCEL`: `1`
     - `VERCEL_ENV`: `production`
     - `PYTHONUNBUFFERED`: `1`
     - `INSTANCE_PATH`: `/tmp/instance`
     - `DATA_DIR`: `/tmp/data`
     - `UPLOAD_DIR`: `/tmp/uploads`

2. **Python 버전**
   - `.python-version` 파일에 `3.11`이 설정되어 있습니다.
   - Vercel이 자동으로 Python 3.11을 사용합니다.

3. **파일 구조 확인**
   ```
   ├── api/
   │   └── index.py       (필수)
   ├── app.py             (필수)
   ├── requirements.txt   (필수)
   ├── vercel.json        (필수)
   └── templates/         (필수)
   ```

## 배포 방법

### 방법 1: Vercel CLI
```bash
npm i -g vercel
vercel login
vercel
```

### 방법 2: GitHub 연동
1. GitHub에 코드 푸시
2. Vercel 대시보드에서 프로젝트 import
3. Environment Variables 설정
4. Deploy 클릭

## 문제 해결

### 404 DEPLOYMENT_NOT_FOUND
- 배포가 완료되었는지 확인
- Vercel 대시보드에서 배포 상태 확인
- 환경 변수가 올바르게 설정되었는지 확인

### 500 FUNCTION_INVOCATION_FAILED
- Vercel Function Logs 확인
- `requirements.txt`에 모든 의존성이 포함되어 있는지 확인
- Python 버전 호환성 확인

### 데이터가 저장되지 않는 문제 (중요)

Vercel 서버리스 환경에서는 로컬 파일시스템이 영구적이지 않습니다. `DATABASE_URL` 이 설정되어 있지 않으면 `/tmp` 경로의 SQLite를 임시로 사용하게 되어, 콜드 스타트(인스턴스 재시작) 시 데이터가 사라집니다.

영구 저장을 위해 다음 중 하나를 사용하세요:

1) PostgreSQL (권장)
- Neon, Supabase, RDS 등 어떤 PostgreSQL이든 사용 가능
- Vercel 프로젝트 설정 > Environment Variables 에 `DATABASE_URL` 추가
  - 예: `postgresql://USER:PASSWORD@HOST:PORT/DBNAME?sslmode=require`

2) MySQL
- 동일하게 `DATABASE_URL` 에 MySQL 커넥션 문자열 설정

설정 후 재배포하면, 서버리스에서도 데이터가 사라지지 않습니다.


## 관리자 계정
- 아이디: `admin`
- 비밀번호: `admin123!@#`

