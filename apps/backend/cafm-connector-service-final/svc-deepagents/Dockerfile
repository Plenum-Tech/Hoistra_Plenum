FROM python:3.12-slim

WORKDIR /app

COPY pyproject.toml .
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .

EXPOSE 8008

CMD ["uvicorn", "src.api.main:app", "--host", "0.0.0.0", "--port", "8008"]
