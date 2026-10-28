FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir . "fastmcp>=2.13"
EXPOSE 8080
CMD ["python", "ga_http_server.py"]
