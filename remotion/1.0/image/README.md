# Remotion Studio image

Source for `ghcr.io/greffon/remotion-studio:1.0`, referenced by
[`../docker-compose.yml`](../docker-compose.yml).

The greffer only pulls images (it never builds), and Remotion ships no upstream
Studio image, so we build and publish our own. CI handles this on changes to
this directory via
[`publish-remotion-image.yml`](../../../.github/workflows/publish-remotion-image.yml).

## What's inside

- Node 22 + the Chrome Headless Shell system libs Remotion needs for preview
  and rendering.
- A minimal starter Remotion project (`src/`) with one `HelloWorld` composition.
- `npx remotion browser ensure` runs at build time so the Headless Shell is
  baked in (fast, offline-capable first render).

The container starts Remotion Studio bound to `0.0.0.0:3000`. The
`project_data` named volume is seeded from the baked project on first run and
then persists the user's compositions.

## Build / test locally

```bash
docker build -t ghcr.io/greffon/remotion-studio:1.0 .
docker run --rm -p 3000:3000 ghcr.io/greffon/remotion-studio:1.0
# Studio: http://localhost:3000
# Headless render check:
#   docker exec <id> npx remotion render src/index.ts HelloWorld /tmp/out.mp4
```

## Licensing

Remotion is source-available under the [Remotion License](https://remotion.dev/license),
not OSI open source. Free for individuals, non-profits, and for-profit orgs
with up to 3 employees; larger for-profit orgs need a company license from
[remotion.pro](https://remotion.pro). Deployers are responsible for their own
compliance.

## Updating Remotion

Bump the pinned `4.0.x` versions in `package.json` (keep `remotion`,
`@remotion/cli`, and React in lockstep), rebuild, and re-run the local render
check before merging. CI republishes the `:1.0` tag on merge to `main`.
