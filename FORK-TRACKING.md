# Fork tracking

Open follow-ups for the `marlon-costa-dc/rope` fork. Update this file whenever an item changes.

## Working rule

- External PRs are followed until their cycle ends (merged or closed).
- Only remote branches are kept. A local worktree is removed as soon as its work is pushed, and recreated when the branch needs work again.
- Unique local work is first pushed to a `preserve/*` ref, judged, then adopted or retired.

## Fork state

- `dc-use` is the downstream integration branch. Use the full producer commit
  or the artifact SHA-256 when comparing installations. On 2026-11-22 it held
  every commit of `python-rope/rope:master` (GitHub compare: 0 ahead, 340 behind).
- The fork's `master` holds only fork CI (`sync-upstream-main.yml`, task
  checker permission), merged into `dc-use` by #62. The `main` mirror that
  `sync-upstream-main.yml` should maintain does not exist yet; check the workflow
  before relying on it.
- Audit 2026-11-22: all 11 open upstream PRs below are already in `dc-use`
  (same or equivalent code and tests). New upstream work is cut from
  `upstream/master` and lands in `dc-use` through its own PR.
- The newest published release is
  [1.15.2+dc.9](https://github.com/marlon-costa-dc/rope/releases/tag/1.15.2%2Bdc.9);
  its release notes name the producer commit. Every later change to `dc-use`
  ships under a new local version.

## Upstream contributions

Merged: [#845](https://github.com/python-rope/rope/pull/845),
[#921](https://github.com/python-rope/rope/pull/921), and
[#922](https://github.com/python-rope/rope/pull/922).
[#920](https://github.com/python-rope/rope/pull/920) and
[#938](https://github.com/python-rope/rope/pull/938) were closed; any upstream
follow-up should be split by behavior for review.

Open with the maintainers:

- [ ] python-rope/rope#960 — Preserve relative imports and aliases when moving globals (`fix/upstream-move-global-aliases`)
- [ ] python-rope/rope#919 — Type saveit so decorated members keep their return type (`pr/typed-saveit`)
- [ ] python-rope/rope#918 — Add match_filter and import_filter hooks to Restructure (`pr/restructure-hooks`)
- [ ] python-rope/rope#911 — Route filesystem queries through the fscommands owner (`pr/fscommands-routing`)
- [ ] python-rope/rope#910 — Honor namespace-package precedence when resolving imports (`pr/namespace-precedence`)
- [ ] python-rope/rope#909 — Compute scope regions from native AST positions (`pr/scope-regions`)
- [ ] python-rope/rope#908 — Guard type-hint providers and infer X | Y union annotations (`pr/type-hinting-guards`)
- [ ] python-rope/rope#907 — Walk positional-only, keyword-only and annotated parameters in patchedast (`pr/signature-tokens`)
- [ ] python-rope/rope#906 — Find textual occurrence offsets through the tokenizer (`pr/occurrences-tokenizer`)
- [ ] python-rope/rope#882 — patchedast: locate multi-line f-strings, replacement fields and match-sequence delimiters by parser coordinates (`pr/parser-coordinates`)
- [ ] python-rope/rope#844 — Fix patchedast aborting on ''' inside a double-quoted f-string (`fix/joinedstr-triple-quote-in-body`)

Planned upstream PRs (behaviour-scoped, cut from `upstream/master`; FLEXT bead
`flext-2lout`):

- [ ] C3 MRO, split from the closed #920: `get_mro` and the C3 merge with
  diamond tests; builtin runtime bases; `UnresolvedBase`; type-hint MRO.
- [ ] `TYPE_CHECKING` branch inference (PEP 484).
- [ ] Nested-class `MoveClass` with `destination_offset`, then class-method
  transfer (`MoveMethod.get_class_changes`).
- [ ] `Project(config_files=False)`, `BuiltinModule.get_scope`, and the
  scope-region, offset and token-index memos, each on its own.
- [ ] External class data, stacked on the source-binding index.

Fork-only, not proposed upstream: `rope/base/externaldata.py` performance (#61),
which depends on the fork-only external class data.

## Consumer propagation

Update the Rope pin in FLEXT Infra's codegen configuration and propagate it
through the native generation and upgrade flow. Verify the installed producer
commit and run the consumer refactorings from the new environment. A lockfile
alone does not prove what a consumer imports.

Keep historical `datacosmos` and `Sync` refs until dependent locks and residual
contracts have been reconciled. The MCB lock still refers to `datacosmos`.
