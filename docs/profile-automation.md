# Profile activity updates

The profile README updates daily through `.github/workflows/update-readme.yml`.
It can also be refreshed with the workflow's **Run workflow** button.

`scripts/build_profile_readme.py` discovers activity without a repository allowlist:

- Public PRs and issues authored by `myeolinmalchi` are searched across GitHub,
  including all result pages. Large searches are split into update-time windows
  to avoid GitHub's 1,000-result search limit.
- GitHub's public profile commit contributions for the past year supply the
  latest credited commit day for each repository. This includes co-authored
  work even when another person authored the merged PR. Crowded time windows
  are split to avoid the contribution API's 100-repository limit.
- Public repositories owned by the user supply maintained projects. Merged
  authored PRs and credited commits supply contributed projects, deduplicated
  by repository. Updating this profile repository does not feature itself.

The README shows the four most recently updated contribution entries and ten
recent projects. These display limits do not restrict repository discovery.
Open PRs, merged PRs, and open issues keep their existing labels. Closed issues
and closed unmerged PRs are omitted. Commit credit is labeled **Commit
contribution** and links to the profile activity that establishes it; it does
not imply that the user's original PR was merged. Dates follow GitHub's
contribution records.

Private repository details are excluded even if the token can read them. API
errors or incomplete results fail the workflow before the README is written.
The workflow uses its existing `GITHUB_TOKEN`; no additional secret is needed.

To check a change locally:

```sh
python3 -m unittest discover -s tests -v
python3 scripts/build_profile_readme.py --dry-run
```

The live dry run requires an authenticated `GITHUB_TOKEN` or `GH_TOKEN` with
access to public GitHub APIs. It prints the generated README without editing it.
