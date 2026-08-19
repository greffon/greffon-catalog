# Postgres with upstream OpenFamily's base schema baked into initdb. The
# server's runMigrations() only applies incremental ALTERs; base tables come
# from server/schema.sql, which upstream bind-mounts into
# docker-entrypoint-initdb.d. The catalog can't mount host files, so bake it.
# Keep OPENFAMILY_TAG + OPENFAMILY_SHA256 in sync with ../openfamily/Dockerfile
# (see ../README.md).
ARG OPENFAMILY_TAG=v1.2.0
ARG OPENFAMILY_SHA256=99c0ec418b8d7b589a13aa007d6c79a4168626f2a7b427aae8afbb8acfad7b52

FROM alpine:3.20 AS src
ARG OPENFAMILY_TAG
ARG OPENFAMILY_SHA256
ADD --checksum=sha256:${OPENFAMILY_SHA256} https://github.com/NexaFlowFrance/OpenFamily/archive/refs/tags/${OPENFAMILY_TAG}.tar.gz /tmp/src.tar.gz
RUN mkdir -p /src && tar -xzf /tmp/src.tar.gz -C /src --strip-components=1

FROM postgres:16-alpine
COPY --from=src /src/server/schema.sql /docker-entrypoint-initdb.d/10-schema.sql
