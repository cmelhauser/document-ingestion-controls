ARG PYTHON_IMAGE=python:3.13-slim
FROM ${PYTHON_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --require-hashes -r requirements.lock
COPY . ./
RUN useradd --system --uid 10001 --home /nonexistent platform && chown -R platform:platform /app
USER 10001
EXPOSE 9443
ENTRYPOINT ["python", "scripts/business_platform_server.py"]
CMD ["/config/client-platform.yaml", "--enable"]
