# The API. Agents run here; code under test never does.
FROM python:3.12.11-slim-bookworm AS build
RUN pip install --no-cache-dir uv==0.13.1
WORKDIR /build
# Same path in both stages: console scripts keep a valid shebang.
ENV UV_PROJECT_ENVIRONMENT=/opt/venv
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
RUN uv sync --frozen --no-dev --extra gcp --no-editable

FROM python:3.12.11-slim-bookworm
RUN useradd --uid 10001 --no-create-home app
COPY --from=build /opt/venv /opt/venv
ENV PATH="/opt/venv/bin:$PATH" PYTHONUNBUFFERED=1
USER 10001
EXPOSE 8080
CMD ["uvicorn", "codesentry.main:app", "--host", "0.0.0.0", "--port", "8080"]
