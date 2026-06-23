# freight-audit REST API image.
FROM python:3.11-slim

# System deps: tesseract for OCR; libgl/glib for opencv-python-headless.
RUN apt-get update && apt-get install -y --no-install-recommends \
        tesseract-ocr libgl1 libglib2.0-0 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir ".[ocr,textract,synth,api]"

# Persist the SQLite store + API keys outside the image layer.
RUN mkdir -p /data
ENV FREIGHT_AUDIT_DB=/data/freight_audit.db \
    FREIGHT_AUDIT_KEYS=/data/api_keys.json
VOLUME ["/data"]

EXPOSE 8000
CMD ["uvicorn", "freight_audit.api:app", "--host", "0.0.0.0", "--port", "8000"]
