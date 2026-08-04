# Rust parity workload — multi-stage: build with the toolchain, run slim.
FROM rust:1.96-slim AS build
WORKDIR /src
COPY Cargo.toml Cargo.lock ./
COPY migrations ./migrations
COPY src ./src
RUN cargo build --release

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /src/target/release/shortener-rs /usr/local/bin/shortener-rs
ENV SHORTENER_DB=/data/shortener.db SHORTENER_HOST=0.0.0.0 SHORTENER_PORT=8788
VOLUME /data
EXPOSE 8788
HEALTHCHECK --interval=15s --timeout=3s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8788/health/ready || exit 1
CMD ["shortener-rs"]
