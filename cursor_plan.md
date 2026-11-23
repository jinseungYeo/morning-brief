# Cursor 인수인계 문서 — AI 기반 뉴스 요약 및 분석 서비스

> 이 문서는 지금까지 진행한 작업을 Cursor가 이어받을 수 있도록 정리한 것입니다.
> 핵심 컨셉: 링크 단건 요약 도구가 아니라, **매일 아침 자동 데일리 브리핑 서비스**.

## 현재 기준 상태 (재구성 후)

- 스택: FastAPI + PyMySQL + MySQL(XAMPP) + python-dotenv + pytest
- 완료: `articles` CRUD, `summaries` 조건 처리 API, 실서비스형 웹 화면(`static/index.html`)
- 수정 완료:
  1. UNIQUE 충돌 `IntegrityError(1062)` → `400` 중복 응답
  2. `people_orgs` / `keywords` 응답을 문자열 대신 배열로 정규화
- 다음: RSS CRUD/수집기 → 브리핑 수동 생성 → 스케줄러 (구현 전 계획 승인 필요)

## API

| 메서드 | 경로 | 성공 | 실패 |
|---|---|---|---|
| POST | `/articles` | 201 | 400, 404 |
| GET | `/articles?user_id=&page=&size=` | 200 | 400 |
| GET | `/articles/{article_id}` | 200 | 404 |
| PATCH | `/articles/{article_id}` | 200 | 400, 404 |
| DELETE | `/articles/{article_id}` | 204 | 404 |
| POST | `/articles/{article_id}/summaries` | 201 | 400, 404 |
| GET | `/articles/{article_id}/summaries` | 200 | 404 |

## 실행

```bash
pip install -r requirements.txt
# schema.sql 적용 후
python -m uvicorn app.main:app --reload
python -m pytest -v
```

`.env` 예시는 `.env.example` 참고. 로컬 XAMPP 기본값은 `root` / 빈 비밀번호.

## 데일리 브리핑 (아직 미구현)

우선순위:
1. `rss_sources` CRUD + feedparser 수집
2. `POST /briefings/generate` 수동 생성
3. 스케줄러 자동 생성

설계 결정점은 대화에서 추천안을 확인한 뒤 코드 작성.
