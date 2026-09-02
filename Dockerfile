FROM python:3.11-slim
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends curl && rm -rf /var/lib/apt/lists/*
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8000
ENV PORT=8000
CMD ["gunicorn", "app:app", "--bind", "0.0.0.0:8000", "--timeout", "120", "--workers", "2"]