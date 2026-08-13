FROM python:3.12-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

ENV PYTHONPATH=/app
ENV DATABASE_URL=postgresql+psycopg2://fortuner:fortuner@db:5432/fortuner
ENV COLLECTOR_MODE=fixture
ENV ENABLE_SCHEDULER=true

RUN mkdir -p /app/data/raw

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
