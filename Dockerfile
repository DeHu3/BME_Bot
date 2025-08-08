# Minimal, fast image
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# Install deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy code
COPY bot ./bot

# Run webhook server (Cloud Run listens on $PORT)
CMD ["python", "-m", "bot.webhook_app"]
