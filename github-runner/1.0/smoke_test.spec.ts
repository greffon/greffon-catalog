import { expect, test, type APIRequestContext } from '@playwright/test';

const token = process.env.GITHUB_RUNNER_SMOKE_TOKEN;
const scope = process.env.GITHUB_RUNNER_SMOKE_SCOPE ?? 'repo';
const repo = process.env.GITHUB_RUNNER_SMOKE_REPO;
const org = process.env.GITHUB_RUNNER_SMOKE_ORG;
const namePrefix = process.env.GITHUB_RUNNER_SMOKE_NAME_PREFIX;
const githubHost = process.env.GITHUB_RUNNER_SMOKE_HOST ?? 'github.com';
const pageSize = 100;

function apiBaseUrl() {
  return githubHost === 'github.com'
    ? 'https://api.github.com'
    : `https://${githubHost}/api/v3`;
}

function runnersPath(page: number) {
  if (scope === 'org') {
    return `/orgs/${org}/actions/runners?per_page=${pageSize}&page=${page}`;
  }

  return `/repos/${repo}/actions/runners?per_page=${pageSize}&page=${page}`;
}

async function findRunnerByPrefix(
  request: APIRequestContext,
  authToken: string,
  prefix: string,
) {
  for (let page = 1; page <= 10; page += 1) {
    const response = await request.get(`${apiBaseUrl()}${runnersPath(page)}`, {
      headers: {
        Accept: 'application/vnd.github+json',
        Authorization: `Bearer ${authToken}`,
        'X-GitHub-Api-Version': '2022-11-28',
      },
      timeout: 15_000,
    });

    expect(response.ok()).toBeTruthy();
    const body = await response.json();
    const runners = body.runners ?? [];
    const runner = runners.find((candidate: { name?: string }) =>
      candidate.name?.startsWith(prefix),
    );

    if (runner) {
      return runner;
    }

    if (runners.length < pageSize) {
      return null;
    }
  }

  return null;
}

test.describe('GitHub Actions Runner', () => {
  test('registered runner appears online in GitHub', async ({ request }) => {
    const missing = [
      !token && 'GITHUB_RUNNER_SMOKE_TOKEN',
      scope === 'repo' && !repo && 'GITHUB_RUNNER_SMOKE_REPO',
      scope === 'org' && !org && 'GITHUB_RUNNER_SMOKE_ORG',
      !namePrefix && 'GITHUB_RUNNER_SMOKE_NAME_PREFIX',
    ].filter(Boolean);

    test.skip(
      missing.length > 0,
      `Set ${missing.join(', ')} to check a deployed runner through the GitHub API.`,
    );

    expect(['repo', 'org']).toContain(scope);

    await expect
      .poll(
        async () => {
          const runner = await findRunnerByPrefix(request, token!, namePrefix!);
          return runner?.status ?? 'missing';
        },
        {
          message: `Runner with prefix "${namePrefix}" should be online`,
          timeout: 120_000,
          intervals: [5_000, 10_000],
        },
      )
      .toBe('online');
  });
});
