FROM python:3.12-slim

WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1

# Tesseract reads text from image attachments; gosu drops root privileges in the entrypoint.
RUN apt-get update \
    && apt-get install -y --no-install-recommends tesseract-ocr gosu \
    && rm -rf /var/lib/apt/lists/* \
    && useradd --system --uid 10001 --home-dir /app app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# The entrypoint fixes folder ownership, then runs the app as the unprivileged "app" user.
# (sed removes Windows line endings, which would break the script's shebang line.)
RUN sed -i 's/\r$//' docker-entrypoint.sh && chmod +x docker-entrypoint.sh \
    && mkdir -p /data /app/logs /app/data && chown -R app:app /data /app/logs /app/data

# OLLAMA_HOST (read by the ollama client) points to the LLM server; see docker-compose.yml.
EXPOSE 8000
ENTRYPOINT ["/app/docker-entrypoint.sh"]
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]
