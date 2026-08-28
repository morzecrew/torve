# The engine repo's gates-side socket image (RFC 0017 §2a): the acceptance
# battery is uv-driven and its conformance tests drive containers, so the
# image carries git, uv and the docker CLI; the daemon is the host's,
# mounted by runtime.docker: socket. Thin per D-17.8 — no harness.
FROM python:3.13-slim
RUN apt-get update \
 && apt-get install -y --no-install-recommends git ca-certificates \
 && rm -rf /var/lib/apt/lists/*
ADD https://download.docker.com/linux/static/stable/x86_64/docker-27.3.1.tgz /tmp/docker.tgz
RUN tar -xzf /tmp/docker.tgz -C /tmp && mv /tmp/docker/docker /usr/local/bin/docker \
 && rm -rf /tmp/docker /tmp/docker.tgz
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /usr/local/bin/
