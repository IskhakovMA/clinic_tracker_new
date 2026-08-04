# GitHub Security Baseline

Repository: `applehooked-cmd/clinic_tracker_new`.

Base commit: `df750016241fd7269705d4a79f790db6f25396ee`.

This document records read-only capability checks and the post-merge plan for
`SEC-2026-004`. No GitHub settings were changed in this branch.

## Capability Matrix

| Capability | Result | Evidence | Notes |
|---|---|---|---|
| Repository visibility | ENABLED | `visibility=PRIVATE`, `isPrivate=true` | Owner type is `User` (`applehooked-cmd`). |
| Default branch | ENABLED | `defaultBranchRef.name=main` | Current target branch. |
| GitHub Actions repository permissions API | NOT VERIFIED | REST endpoint returned 404 | The token lacks admin access or the endpoint is unavailable to this account context. |
| Default workflow permissions API | NOT VERIFIED | REST endpoint returned 404 | Current workflow defaults cannot be verified read-only. |
| Branch protection for `main` | NOT VERIFIED | REST endpoint returned 404 | Could mean no protection or insufficient permission; do not treat as enabled. |
| Repository rulesets | UNAVAILABLE_BY_PLAN | REST endpoint returned 403: upgrade to GitHub Pro or make repo public | Repository must remain private; no visibility change is proposed. |
| Dependabot alerts | DISABLED | REST endpoint returned 403: alerts disabled | Dependabot config is added, but alerts/settings require post-merge owner action. |
| Code scanning alerts | DISABLED | REST endpoint returned 403: code scanning not enabled | CodeQL workflow is not added to avoid a failing required check. |
| Secret scanning alerts | NOT VERIFIED | REST endpoint returned 404 | Likely unavailable or not enabled for this private personal repository. |
| GitHub Dependency Review | NOT VERIFIED | Dependency graph/review availability not confirmed | `dependency-review` workflow is not added to avoid an unsupported check. |
| Local CI baseline | PARTIAL | `.github/workflows/ci.yml` | Workflows added; branch protection still pending. |
| SAST baseline | PARTIAL | `sast-bandit` job | Bandit runs without GitHub Advanced Security. |
| Secret scanning baseline | PARTIAL | `secret-scan` job | Gitleaks scans tree and history; not a full replacement for push protection. |

## Added Checks

The CI workflow publishes these job/check names:

- `backend-tests`
- `frontend-security`
- `workflow-policy`
- `dependency-audit`
- `sast-bandit`
- `secret-scan`

`dependency-review` and CodeQL are not added in this branch because private
repository capability was not confirmed and code scanning is currently disabled.

## Actions and Pinning

All external actions are pinned to full 40-character commit SHAs:

| Action | Pinned SHA | Version comment |
|---|---|---|
| `actions/checkout` | `11d5960a326750d5838078e36cf38b85af677262` | `v4` |
| `actions/setup-python` | `a26af69be951a213d495a4c3e4e4022e16d87065` | `v5` |
| `actions/setup-node` | `49933ea5288caeca8642d1e84afbd3f7d6820020` | `v4` |

No action uses `@main`, `@master`, `@v4`, `@v5`, a short SHA, or a mutable
branch reference.

## Workflow Security Policy

The workflow policy test enforces:

- no `pull_request_target`;
- no self-hosted runners;
- top-level `permissions: contents: read`;
- no write permissions;
- external actions pinned by full SHA;
- checkout uses `persist-credentials: false`;
- no repository secrets in workflows;
- no direct `${{ github.event.* }}` use inside shell steps;
- no `curl | sh` or `wget | sh`;
- no mutable container images.

## Post-Merge Settings Plan

After this PR is merged and checks are visible on `main`, apply repository
settings in a separate confirmed step:

1. Enable GitHub Actions if disabled, with default workflow token permissions set
   to read-only.
2. Protect `main` or use rulesets when available.
3. Require pull requests before merging.
4. Require at least 1 approval if the account plan and single-owner workflow
   allow it.
5. Dismiss stale approvals.
6. Require Code Owners review if supported.
7. Require conversation resolution.
8. Require status checks:
   - `backend-tests`
   - `frontend-security`
   - `workflow-policy`
   - `dependency-audit`
   - `sast-bandit`
   - `secret-scan`
   - `dependency-review` only if later enabled and green
   - CodeQL only if later enabled and green
9. Require branch to be up to date before merging if it does not create an
   unstable process.
10. Block force pushes.
11. Block branch deletion.
12. Do not allow bypass where the plan supports this.
13. Include administrators where supported.
14. Prohibit direct pushes to `main`.

If branch protection or rulesets remain unavailable for this private personal
repository, keep CI and use PR-only discipline as a temporary procedural
control. Long-term options are GitHub Pro or moving the repository into an
organization with the required plan. Do not make the repository public solely to
obtain free protection features.

## SEC-2026-004 Status

Status in this feature branch: PARTIAL.

Reason: CI/security workflows, Dependabot config, CODEOWNERS, SECURITY.md, PR
template, and a settings plan are added. Branch protection and GitHub-native
security features are not applied yet and require a separate post-merge settings
step.
