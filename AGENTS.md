# Clinic Tracker development instructions

Use subagents at every development stage. Assign independent checks in parallel when possible, and give each subagent a concrete scope, expected evidence, and any limits on edits. The main agent owns reconciliation, source and index changes, GitHub publication, and cleanup. It must verify delegated findings against the relevant code and results before reporting completion.

Use the private `IskhakovMA/fixes` repository as the source of truth. Start each stage in a fresh temporary worktree based on a verified remote commit. Test the exact candidate, preserve the review evidence and publish the accepted revision under the owner's release instructions. Verify the remote commit and uploaded evidence before removing local worktrees or the only local copy of any artifact.

Keep credentials, private environment files, real clinical data and service state out of Git and GitHub archives. Exercise application tests and migrations only on synthetic or expressly authorized test data. Keep release verification, production deployment and approval to change clinical behavior as separate decisions.
