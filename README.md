# gwangju-eodi

광주·전남 문화·생활 AI 추천 앱입니다.

### 폴더
- `docs/index.html` : 현재 앱 본체
- `docs/data/` : 자동수집 결과가 저장되는 곳
- `scripts/update_data.py` : TourAPI/KOPIS 자동 수집기
- `.github/workflows/update-data.yml` : 하루 2회 자동 실행

### GitHub Secrets
아래 두 이름이 Repository secrets에 있어야 합니다.
- `DATA_GO_KR_KEY`
- `KOPIS_API_KEY`

실제 인증키는 코드에 넣지 않습니다.
