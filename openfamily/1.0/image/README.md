# OpenFamily greffon images

The OpenFamily catalog entry needs two images that upstream does not publish in a
greffer-compatible shape. Both are **pure packaging**: they build unmodified
upstream source at a pinned release tag and only flip flags upstream already
supports. No source fork.

| Image | Context | Why |
|-------|---------|-----|
| `ghcr.io/greffon/openfamily:<tag>` | `openfamily/` | Upstream's published client image bakes `http://localhost:3001` as the API URL at build time, and its compose serves the SPA on a second public host. We rebuild the client with **empty `VITE_*`** (relative API + `window.location` WebSocket) and run the server in its built-in single-origin mode (`SERVE_CLIENT_DIR`), so one container serves SPA + `/api` + `/ws` on one port. |
| `ghcr.io/greffon/openfamily-db:<tag>` | `openfamily-db/` | The server's `runMigrations()` only applies incremental migrations; the base tables come from `server/schema.sql`, which upstream mounts into `docker-entrypoint-initdb.d`. The catalog can't mount host files, so we bake it into the image. |

Each image has its own self-contained build context (the Dockerfile fetches the
upstream release tarball itself), so a plain `docker build <dir>` works with no
external checkout. PR CI relies on that: the smoke job builds every
`ghcr.io/greffon/*` tag referenced by the compose from `image/<repo-basename>/`
before deploying, so the candidate images are exercised before merge.

## Pinning

`<tag>` tracks an upstream OpenFamily release tag (e.g. `v1.2.0`). Bumping it
means updating, in one PR:

1. `OPENFAMILY_TAG` and `OPENFAMILY_SHA256` defaults in `openfamily/Dockerfile`
   and `openfamily-db/Dockerfile` (`curl -sL <tarball-url> | shasum -a 256`)
2. the two `image:` tags in `../docker-compose.yml`
3. the two `tags:` lines in `.github/workflows/publish-openfamily-images.yml`
4. the pinned logo URL in `../metadata.json`

CI then smokes the bumped images on the PR, and the publish workflow pushes them
to GHCR on merge to main.

## First-publish visibility

GHCR creates brand-new packages as **private**, and production greffers pull
anonymously (they hold no registry credentials). After the very first publish,
flip both `openfamily` and `openfamily-db` to public in the GitHub org's
package settings, then confirm an anonymous pull works. Until then the entry
deploys in PR CI (which builds the images locally) but fails to pull on a real
node.

## Build locally

```bash
docker build -t ghcr.io/greffon/openfamily:v1.2.0    openfamily/
docker build -t ghcr.io/greffon/openfamily-db:v1.2.0 openfamily-db/
```
