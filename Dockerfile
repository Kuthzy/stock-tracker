FROM python:3.12-slim
ENV PYTHONUNBUFFERED=1 TZ=America/New_York
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY . .
EXPOSE 8080
HEALTHCHECK --interval=2m --timeout=10s CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8080/health')"
CMD ["python", "-m", "tracker", "run"]
