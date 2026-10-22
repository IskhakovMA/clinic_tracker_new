## Merge gate (required — branch protection is not available on this plan)

- [ ] The `release-gate` check of the **latest** commit of this PR is green (link the run).
- [ ] Every gate it aggregates ran: backend shards + `backend-tests-verify` (all collected tests ran once and passed), `backend-smoke`, `frontend-security`, `workflow-policy`, `dependency-audit`, `sast-bandit`, `secret-scan`.
- [ ] An independent reviewer (not the author) approved this exact commit SHA.
- [ ] No new `.gitleaksignore` / `security-audit/bandit-allowlist.json` entry, or each new entry has a written review reason.
- [ ] The merge is a normal merge of the reviewed SHA; no force push to the target branch.

## Checklist

- [ ] Tests were run or the reason they were not run is documented.
- [ ] Database migrations are described, or this PR has none.
- [ ] RBAC and patient scope impact is checked.
- [ ] CSRF is checked for new or changed write endpoints.
- [ ] PHI is not written to logs, audit, localStorage, screenshots, or artifacts.
- [ ] Secrets, tokens, `.env` values, databases, backups, and WAL/SHM files are not included.
- [ ] Dependency changes are explained.
- [ ] Security impact is described.
- [ ] Screenshots use only synthetic data.
- [ ] Documentation is updated where needed.
- [ ] Rollback notes are included when relevant.
