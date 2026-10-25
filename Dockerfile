FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COMPANY_DETECTIVE_HOST=0.0.0.0

WORKDIR /app

# OCR stays inside the container, including Chinese language data.
ARG DEBIAN_MIRROR=https://mirrors.tuna.tsinghua.edu.cn
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get -o Acquire::Retries=2 -o Acquire::http::Timeout=30 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-chi-sim \
    && rm -rf /var/lib/apt/lists/*
ARG PYPI_INDEX=https://pypi.tuna.tsinghua.edu.cn/simple
RUN pip install --no-cache-dir --index-url "${PYPI_INDEX}" Pillow==12.2.0

# Deliberately exclude credentials and the local evidence database.
COPY server.py engine.py investigation.py monitoring.py credibility.py briefing.py scope_guard.py casework.py evidence_scope.py ocr_service.py ./
COPY index.html app.js style.css README.md ./
COPY tools/verify_wsa.py ./tools/verify_wsa.py

EXPOSE 8765

COPY docker-entrypoint.sh /usr/local/bin/company-detective
RUN chmod +x /usr/local/bin/company-detective

ENTRYPOINT ["/usr/local/bin/company-detective"]
