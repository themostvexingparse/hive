---
title: Reviews before work reaches main
keywords: reviews, code review, integration.review, review_verdict, approve work
see: roles, presets, leases
---
# Reviews

A review means another agent must approve a programmer's work before it reaches `main`.
Turn it on in `hive.toml`:

```sh
hive config set integration.review reviewer
```

- `off` (default): work merges as soon as its checks pass.
- `orchestrator`: the orchestrator reviews every piece of work.
- `reviewer`: an agent with the `reviewer` role reviews; if no reviewer other than the
  author exists, the orchestrator does.

The `team` and `strict` presets turn on `reviewer` reviews (`hive help presets`).

## How it goes

1. The programmer's `lease_release` pushes its branch, and its task enters **review**. It
   keeps its file leases, so nobody else changes the files under review.
2. The reviewer is woken with the commit to inspect, and gives its verdict (approve, or
   changes with notes). Nobody reviews their own task.
3. **Approved:** the programmer's daemon finishes the merge by itself (rebase, checks, merge
   into `main`). An approval counts only for the review it was given in.
4. **Changes requested:** the task goes back to `in_progress`, and the programmer is woken
   with the notes.

If a reviewer is removed (`hive agents rm`), the tasks it was reviewing go to another
reviewer.
