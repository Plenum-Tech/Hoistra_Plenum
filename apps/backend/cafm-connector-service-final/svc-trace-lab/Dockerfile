# Hoist Trace Lab: its own image, process and container (G0 addendum 2.1).
#
# Build context: apps/backend/cafm-connector-service-final (the schema-mapper precedent in
# docker-compose.single-url.local.yml), so svc-deepagents' code can be copied BESIDE this service
# and imported as a library. Nothing in svc-deepagents is edited; its own image is untouched.
#
#   docker build -f svc-trace-lab/Dockerfile apps/backend/cafm-connector-service-final
#
# No .env file is copied: every setting arrives from the environment (compose or the platform).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# svc-deepagents' runtime dependencies, read from its own pyproject.toml, plus Trace Lab's own.
COPY svc-deepagents/pyproject.toml /app/svc-deepagents/pyproject.toml
COPY svc-trace-lab/pyproject.toml /app/svc-trace-lab/pyproject.toml
RUN python -c "import tomllib; da = tomllib.load(open('/app/svc-deepagents/pyproject.toml', 'rb')); tl = tomllib.load(open('/app/svc-trace-lab/pyproject.toml', 'rb')); print(*da['project']['dependencies'], *tl['project']['dependencies'], sep=chr(10))" > /tmp/requirements.txt \
    && pip install --no-cache-dir -r /tmp/requirements.txt \
    && rm /tmp/requirements.txt

# svc-deepagents' package and skills, beside Trace Lab (src/agents/skills.py finds ../skills).
COPY svc-deepagents/src /app/svc-deepagents/src
COPY svc-deepagents/skills /app/svc-deepagents/skills
COPY svc-trace-lab /app/svc-trace-lab

WORKDIR /app/svc-trace-lab
ENV PYTHONPATH=/app/svc-trace-lab:/app/svc-deepagents

EXPOSE 8010

CMD ["uvicorn", "trace_lab.app:app", "--host", "0.0.0.0", "--port", "8010", "--root-path", "/backend/trace-lab", "--proxy-headers", "--forwarded-allow-ips=*"]
