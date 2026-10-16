# BCR IP Search Desk

KIPRIS Plus 기반 로컬 특허·실용신안 검색 데스크. 키 없이도 샘플 모드로 동작한다.

## 실행 (3단계)

```bash
# 1) uv 설치 (최초 1회)
#    macOS:   curl -LsSf https://astral.sh/uv/install.sh | sh
#    Windows: powershell -c "irm https://astral.sh/uv/install.ps1 | iex"

# 2) 설정 파일 준비 (Windows: copy .env.example .env)
cp .env.example .env

# 3) 실행 → http://127.0.0.1:8000 이 자동으로 열린다
uv run ip-desk
```

- 라이브 모드: `.env`에서 `IPDESK_MODE=kipris`, `KIPRIS_API_KEY=발급키`로 바꾼다. 키는 화면에서 입력하지 않는다.
- 키 확인: `uv run ip-desk check`
- 테스트: `uv run pytest`

> 입력한 검색어는 KIPRIS로 전송된다. 미공개 발명의 핵심 표현은 일반 용어로 바꿔 입력할 것.
