# Fork tracking

Open follow-ups for the `marlon-costa-dc/rope` fork. Update this file whenever an item changes.

## Working rule

- External PRs are followed until their cycle ends (merged or closed).
- Only remote branches are kept. A local worktree is removed as soon as its work is pushed, and recreated when the branch needs work again.
- Unique local work is first pushed to a `preserve/*` ref, judged, then adopted or retired.

## Fork state

- `dc-use` = d94d3c1d, release [1.15.2+dc.5](https://github.com/marlon-costa-dc/rope/releases/tag/1.15.2%2Bdc.5) (base: upstream 1.15.2). Publish verification green.
- Remote branches: `dc-use`, `master`, one head per open upstream PR, and `datacosmos`/`Sync` (see below).

## Upstream PRs (python-rope/rope), awaiting maintainer review

All behaviour is already in the fork release. Each fork branch is deleted when its PR merges.

- [ ] python-rope/rope#938 — Fix the warnings the test suite emits at their source (`pr/test-suite-warnings`)
- [ ] python-rope/rope#922 — Classify autoimport project sources by path containment (`pr/autoimport-project-containment`)
- [x] python-rope/rope#921 (merged upstream as 456b210d; branch deleted) — Patch the else suite of try statements that also have a finally
- [ ] python-rope/rope#920 — Resolve inherited class attributes along the C3 method resolution order (`pr/c3-mro`)
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

## Consumer propagation (measured 2026-11-07, installed `.venv`, real rename probe)

- [x] flext root, ai-hub (`dev`), gruponos/merges (`main`), algar-oud-mig = devops-oud-iac (`dev` f470eaa3): 1.15.2+dc.4
- [x] Lock-drift gate in flext-infra `check` (flext-sh/flext-infra#2174)
- [ ] 1.15.2+dc.5 pin in flext-infra `codegen.yaml`, then each consumer `gen` → `upg`
- [ ] flext member `uv.lock` files still resolving `datacosmos` (flext bead `flext-zzynv`)
- [ ] mcb: kind `internal_flext` declared on lane `preserve/chore-rope-dc4-20261107`; `gen` blocked by flext bead `flext-wmzp6` and the absent FLEXT family facades
- [ ] invest (kind `internal_flext`; member vectorbt.pro `third_party_fork`), cosmos-main and docsmos-main: same path as mcb

## Retired local lanes

- `~/flext/.kilo/native/rope` and its codex worktrees: every branch was already on a remote. `b2957ab0` (lazy index with `complete=`) is superseded by the dc.2 index. The global-free scan guard from `rope-zero-global-20261102` was adopted in dc.5. The FLEXT markdown formatting on the fork's docs was not adopted, since the fork follows upstream style.

## Branch retirement

- [ ] `datacosmos`, `Sync`: delete once no consumer lock references them
