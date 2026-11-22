---
name: misra-pr
description: Take a change in this repository from branch to merged PR (checks, Codex review on the current head, merge gates). Use when opening, updating or merging a PR here.
---

# misra PR cycle

Follow "Working with agents" in CONTRIBUTING.md; this is the checklist.

1. Branch from `origin/main` in its own worktree (`git worktree add`). Never edit `docs/`.
2. Use the Node of `.nvmrc`. Run `npm run lint`, `npm run typecheck`, `npm run test:coverage` (100% line and branch).
3. Bump the version once when `package.json`, `pyproject.toml` or a listed Python module changes: package.json, package-lock root, pyproject.toml, `common.VERSION`, CHANGELOG.
4. Commit: never `--no-verify`, no Claude co-author trailer. Fix the cause when a hook fails.
5. Open a draft PR, then `gh pr ready`. Plain title; description with a short intro and bullets, no section titles.
6. Agents stop here and report the PR address and what they did not check. Only the coordinator merges.
7. Merge only when: every check is green (`quality` too), Codex completed on the CURRENT head (comment `@codex review` after a new commit), and 0 unresolved review threads. Then `gh pr merge <n> --repo ravidorr/misra --admin --squash`.
8. After a fix, reply on each thread and resolve it.
9. Company-list writes: `tools/companyctl.py`, dry run first, at most 25 per batch, then a `discover-companies` run to check each first crawl.

Never print a secret, a row, a posting's text or personal data. Report counts only.
