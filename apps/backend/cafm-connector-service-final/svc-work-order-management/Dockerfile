FROM python:3.12-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8007

# Match deploy/supervisord.conf + deploy/nginx.conf location /work-order/
CMD ["uvicorn", "src.app:app", "--host", "0.0.0.0", "--port", "8007", "--root-path", "/work-order"]
