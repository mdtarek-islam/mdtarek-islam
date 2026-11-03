# Profile automation and maintenance

This guide covers the daily workflow that keeps the profile page current.

## Schedule

1. [`daily-portfolio-sync.yml`](../.github/workflows/daily-portfolio-sync.yml) runs each day at 08:00 UTC.
2. GitHub can start a scheduled run up to about 30 minutes late at busy times.
3. Start a run at any time: Actions tab, "Daily portfolio sync", "Run workflow".
4. From a terminal, run `gh workflow run daily-portfolio-sync.yml -R mdtarek-islam/mdtarek-islam`.

## What each run does

1. It runs the script tests. A failed test stops the run and changes nothing.
2. It redraws `assets/london-skyline.svg` from the contribution calendar.
3. It rewrites the project showcase between the `SHOWCASE` markers in the README and refreshes `assets/covers/`.
4. It checks that every SVG is valid XML and that both README markers exist.
5. It commits and pushes only if a file changed. The commit author is MD Tarek Islam.
6. It warns about broken README links without stopping the run.

## Secrets

| Secret | Purpose |
|---|---|
| `PROFILE_STATS_TOKEN` | Reads contributions as the owner, so private activity counts. |
| `EXCLUDE_REPOS` | Comma-separated repository names to hide from the showcase. |
| `GITHUB_TOKEN` | Supplied by GitHub. Lists repositories. Fallback for the stats query. |

`PROFILE_STATS_TOKEN` is a fine-grained token named `profile-stats`. It has read-only Metadata and Contents access to all repositories.

## Maintenance checklist

1. **Renew the token yearly.** When it expires, private activity drops off the skyline. Create a new token with the same settings, then run `gh secret set PROFILE_STATS_TOKEN -R mdtarek-islam/mdtarek-islam`.
2. **Check the run log after a renewal.** Expect "Token source: PROFILE_STATS_TOKEN" and "Private contributions visible: yes".
3. **Watch for a disabled schedule.** GitHub disables scheduled workflows after 60 days without repository activity. Re-enable the workflow in the Actions tab if that happens.
4. **Review dependabot pull requests weekly.** They keep the workflow actions current. Merge them when the checks pass.
5. **Act on failure emails.** GitHub emails the owner when a scheduled run fails. Read the failed step in the run log.
6. **Hide a repository from the showcase.** Add its name to the `EXCLUDE_REPOS` secret. Private repositories never appear.

## How to read the skyline

1. Each building is one week, from Sunday to Saturday. The newest week is on the right.
2. Building height follows that week's contribution total. The busiest week reaches full height. Every other week scales linearly against it.
3. The number on the tallest building is the busiest week's total. The caption names that week as "Peak week of <Sunday date>".
4. Each building has 7 windows, one per day, with Sunday at the top. Brighter windows mark busier days.
5. An empty week keeps a short building with dark windows.

## What the skyline numbers mean

The figures cover the same 52-week window as the calendar.

| Label | Meaning |
|---|---|
| Commits | Commits you authored on the default branch of a public repository. Commits on other branches count only after a merge. |
| Code reviews | Pull request reviews you submitted in public repositories. |
| Pull requests | Pull requests you opened in public repositories. |
| Issues | Issues you opened in public repositories. |
| New repositories | Public repositories you created. Forks do not count. |
| Private | All contributions in private repositories, of every type, as one figure. GitHub does not split private activity by type. |

The six figures do not overlap, so they add up to the calendar total.
