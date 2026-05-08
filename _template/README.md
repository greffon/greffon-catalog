# Greffon Template

Starter files for a new catalog entry. Two ways to use it:

## Option A — `/add-greffon` skill (recommended)

In Claude Code, from anywhere in the catalog repo:

```
/add-greffon <name> [version]
```

The skill copies this template, looks up the upstream `docker-compose.yml`,
infers user-configurable settings, drafts a Playwright smoke test, and
runs the validator. You review the diff and open the PR.

## Option B — copy by hand

```
cp -R _template/1.0 <your-app>/1.0
```

Then walk the three files and replace every `TODO:` comment.

## What's in here

- `1.0/docker-compose.yml` — minimal compose with the platform rules in comments
- `1.0/metadata.json` — two example configurations: a templated public URL (`env` destination) and a required-secret admin password
- `1.0/smoke_test.spec.ts` — Playwright skeleton; the deploy URL comes from an env var

See [../README.md](../README.md) for the full catalog format and
[../../docs/adding-a-greffon.md](../../docs/adding-a-greffon.md) for the deploy-time
transformation rules and the destination-type reference.
