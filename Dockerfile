FROM python:3.12-slim

WORKDIR /app

# Tesseract reads text from image attachments.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# OLLAMA_HOST (read by the ollama client) points to the LLM server; see docker-compose.yml.
EXPOSE 8000
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
