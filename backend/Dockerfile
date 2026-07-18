FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

# 의존성 레이어 (소스 변경과 분리하여 캐시 활용)
COPY requirements.txt ./
RUN pip install --upgrade pip && pip install -r requirements.txt

COPY . .

RUN chmod +x /app/docker-entrypoint.sh

EXPOSE 8000

# 기동 시 alembic upgrade head 선행 후 앱 실행 (docker-entrypoint.sh)
ENTRYPOINT ["/app/docker-entrypoint.sh"]

# 게이트웨이(nginx) 경유만 허용하므로 컨테이너 내부 8000 에서만 대기
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
