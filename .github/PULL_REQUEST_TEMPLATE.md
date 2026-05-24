## Summary

<!-- 1-3 sentences. What does this PR do and why? -->

## Greffon affected

<!-- e.g. `plausible/2.1`, `nextcloud/1.0`, or "tooling" / "docs" -->

## Checklist

- [ ] Commits are [Signed-off-by](https://developercertificate.org/) (DCO) — use `git commit -s`
- [ ] [Conventional Commit](https://www.conventionalcommits.org/) prefix in commit and PR title (`feat:`, `fix:`, `docs:`, `chore:`)
- [ ] Validator passes: `python .github/scripts/validate_catalog.py --dir <greffon>/<version>`
- [ ] If adding/changing a greffon: `smoke_test.spec.ts` updated and passes against a real dev environment
- [ ] Upstream license of the deployed application is permissively-licensed or otherwise OK to publish a recipe for (note in description if unsure)

## Notes for reviewer

<!-- Anything reviewer should look at carefully, or things that aren't obvious from the diff. -->
