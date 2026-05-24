# Security Policy

## Reporting a vulnerability

**Please do not file public GitHub issues for security vulnerabilities.**

Two options for private disclosure:

1. **GitHub Security Advisories** (preferred): https://github.com/greffon/greffon-catalog/security/advisories/new
2. **Email**: `security@greffon.io`

Include:

- A description of the issue
- Steps to reproduce
- Affected versions (catalog entry name + version directory)
- Your suggested impact assessment, if you have one

## What to expect

- **Acknowledgment**: within 3 business days.
- **Initial assessment**: within 7 business days — severity classification and intended fix timeline.
- **Coordinated disclosure**: we'll work with you on a disclosure window before any public advisory.

## Scope

This repository contains docker-compose templates and metadata for third-party applications. The most relevant vulnerability classes here are:

- A catalog template that exposes a user-controlled environment variable in an unsafe context
- A `smoke_test.spec.ts` that leaks credentials or PII
- A template that defaults to a known-insecure configuration of the upstream application

For vulnerabilities in the **upstream applications themselves** (Plausible, Ghost, Nextcloud, etc.), please report to the respective upstream project. We'll happily relay reports if you flag them to us, but we don't patch upstream code from here.

For vulnerabilities in the **greffer worker**, the **manager**, or the **Greffon platform** more broadly, report at [greffon/greffon](https://github.com/greffon/greffon/security/advisories/new) or `security@greffon.io`.

## Supported versions

Each catalog entry has versioned subdirectories. We patch the most recent version of each greffon. Older versions are best-effort.
