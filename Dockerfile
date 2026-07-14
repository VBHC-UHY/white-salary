FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Copy project metadata/source and install dependencies declared in pyproject.
COPY pyproject.toml README.md ./
COPY src/ src/
RUN pip install --no-cache-dir -e ".[memory-vector]"

# Copy runtime entry/config templates
COPY run_server.py .
COPY conf.default.yaml .
COPY prompts/ prompts/

ENV PYTHONPATH=src
ENV PYTHONUNBUFFERED=1

EXPOSE 12400

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=5 \
    CMD python -c "import json,urllib.request; data=json.load(urllib.request.urlopen('http://127.0.0.1:12400/health', timeout=3)); assert data.get('status') == 'ok' and data.get('name') == 'White Salary'" || exit 1

CMD ["python", "run_server.py", "--host", "0.0.0.0", "--port", "12400"]
