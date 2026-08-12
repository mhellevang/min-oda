# Python pinnet til 3.12 (rookiepy mangler wheels for 3.13+). rookiepy brukes
# ikke i headless-drift, men er fortsatt en avhengighet i pyproject.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

WORKDIR /app

# Codex-CLI for LLM-forslagene (jf. min_oda/llm.py): frittstående binær fra
# GitHub-releasen, ikke npm-pakken — npm-veien drar med seg hele nodejs for
# å installere det som uansett er én statisk Rust-binær. Innloggingen ligger
# i et eget volum (CODEX_HOME), se DEPLOY.md.
ARG CODEX_VERSION=0.147.0
ARG TARGETARCH
RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && case "$TARGETARCH" in arm64) arch=aarch64 ;; *) arch=x86_64 ;; esac \
    && curl -fsSL "https://github.com/openai/codex/releases/download/rust-v${CODEX_VERSION}/codex-${arch}-unknown-linux-musl.tar.gz" \
        | tar -xz -C /usr/local/bin \
    && mv "/usr/local/bin/codex-${arch}-unknown-linux-musl" /usr/local/bin/codex \
    && apt-get purge -y curl && apt-get autoremove -y && rm -rf /var/lib/apt/lists/*

# Deterministiske installasjoner, kopier heller enn symlink (bedre i containere).
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONUNBUFFERED=1

# Installer avhengigheter først (eget lag) for å utnytte Docker-cachen når
# bare koden endres.
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-install-project --no-dev

# Kopier resten av prosjektet og installer selve pakken.
COPY . .
RUN uv sync --frozen --no-dev

# Ta vare på default product_types.json et sted som ikke skygges av det
# monterte data-volumet, så entrypoint kan så det inn i et tomt volum.
RUN mkdir -p /app/seed && cp /app/data/product_types.json /app/seed/product_types.json

# Build-versjon (git-SHA) bakes inn av CI. Default "dev" for lokale bygg.
ARG BUILD_VERSION=dev
ENV BUILD_VERSION=${BUILD_VERSION}

EXPOSE 8000

ENTRYPOINT ["/app/docker-entrypoint.sh"]
