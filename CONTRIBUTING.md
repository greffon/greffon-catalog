# Contributing to Greffon Catalog

Thanks for thinking about adding a greffon. This catalog is permissively licensed (Apache 2.0) on purpose — we want contributions to be easy.

## What we accept

- **New greffons**: docker-compose templates for self-hostable, open-source applications. See [README.md § Adding a New Greffon](README.md#adding-a-new-greffon).
- **Fixes** to existing greffons: bugs, config improvements, smoke-test updates.
- **Documentation**: any clarification of the catalog format, the metadata schema, or the Jinja template vars.

## What we don't accept (yet)

- Closed-source applications that require a vendor license to run.
- Applications that don't work behind a reverse proxy (the greffer requires one).
- Custom-built apps without a published upstream `docker-compose.yml` to anchor against.

## Before you open a PR

1. Read [README.md](README.md) and [docs/adding-a-greffon.md](https://github.com/greffon/greffon/blob/main/docs/adding-a-greffon.md) (in the main `greffon` repo) — the catalog format has several constraints the validator enforces.
2. Run the validator locally: `python .github/scripts/validate_catalog.py --dir <greffon-name>/<version>` — until it exits 0.
3. Write a smoke test (`smoke_test.spec.ts`) that asserts the primary user task. CI runs this against a real dev environment.
4. **Sign off your commits with the Developer Certificate of Origin (DCO).** Use `git commit -s` so each commit ends with a `Signed-off-by:` line. The DCO bot enforces this on every PR. See [DCO](https://developercertificate.org/) for the certification text — by signing off you're stating you have the right to contribute the code under the project's license.

## Commit style

[Conventional Commits](https://www.conventionalcommits.org/). Examples:

- `feat(plausible): add 2.1 config with SMTP destinations`
- `fix(nextcloud): strip greffer-local-port from OVERWRITEHOST`
- `docs(readme): clarify smtp destination behavior`

## Code of Conduct

Participation is governed by the [Code of Conduct](CODE_OF_CONDUCT.md) (Contributor Covenant 2.1). Report violations to `security@greffon.io` (or the contact in `CODE_OF_CONDUCT.md`).

## Where to ask questions

- **Discord**: https://discord.gg/vBmhUGPY
- **GitHub Discussions**: in the main [greffon/greffon](https://github.com/greffon/greffon) repo
- **Issues**: file in this repo if it's specific to a catalog entry; in the main repo for platform-wide questions

## Licensing of your contribution

By contributing, you agree your contribution is licensed under the Apache License 2.0 (the same license as this repo). No CLA. The DCO sign-off on your commit is the agreement.
