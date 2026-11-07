# Fork tracking

Open follow-ups for the `marlon-costa-dc/rope` fork. Update this file whenever an item changes.

## Fork state

- `dc-use` = 64cbd65b, release [1.15.2+dc.4](https://github.com/marlon-costa-dc/rope/releases/tag/1.15.2%2Bdc.4) (base: upstream 1.15.2). Publish verification green.

## Upstream PRs (python-rope/rope), awaiting maintainer review

All behaviour is already in the fork release. Each fork branch is deleted when its PR merges.

- [ ] python-rope/rope#938 — Fix the warnings the test suite emits at their source (`pr/test-suite-warnings`)
- [ ] python-rope/rope#922 — Classify autoimport project sources by path containment (`pr/autoimport-project-containment`)
- [ ] python-rope/rope#921 — Patch the else suite of try statements that also have a finally (`pr/try-else-finally-regions`)
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

## Consumer propagation (measured 2026-11-07, installed `.venv`)

- [x] flext root — 1.15.2+dc.4
- [ ] flext-infra pin on `0.12.0-dev` (gates every consumer below; generated `pyproject` pins derive from it)
- [ ] flext member `uv.lock` files — 21 still resolve `datacosmos#7a6494e8` (blocked on flext bead `flext-zzynv`)
- [ ] ai-hub — 1.15.0+dc.12; `editable_sources: rope: ../rope-dc15` to be rewired to the pin
- [ ] algar-oud-mig, devops-oud-iac — 1.15.0+dc.12
- [ ] gruponos/merges (dc-recon) — 1.15.2+dc.2, own codegen pin
- [ ] cosmos-main, docsmos-main, invest, mcb — no `.venv`; locks pin dc.2 or `datacosmos`

## Branch retirement

- [ ] `datacosmos`, `Sync` — delete once no consumer lock references them
- [ ] `~/rope-dc15` back to `dc-use` once ai-hub and the editable consumers stop pointing at it
