FROM python:3.12.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PIP_DISABLE_PIP_VERSION_CHECK=1

COPY vectorbt/requirements.in /tmp/requirements.in

RUN python -m pip install --no-cache-dir --only-binary=:all: \
        --requirement /tmp/requirements.in \
    && mkdir -p /opt/strategy-lab \
    && python -m pip freeze --all > /opt/strategy-lab/vectorbt-freeze.txt

WORKDIR /workspace

COPY vectorbt/run_fixture.py /workspace/run_fixture.py

ENTRYPOINT ["python", "/workspace/run_fixture.py"]
