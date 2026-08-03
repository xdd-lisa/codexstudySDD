## 1. Trending Parsing and Evidence

- [x] 1.1 Add constants and typed helpers for weekly GitHub Trending requests, canonical repository identities, and safe GitHub API headers.
- [x] 1.2 Implement bounded Trending markup parsing that extracts repository identity, page rank, and weekly period stars and rejects missing required evidence.
- [x] 1.3 Add fixture-based tests for valid entries, duplicate representations, malformed markup, and unavailable period-star evidence.

## 2. Repository Filtering and Ranking

- [x] 2.1 Implement bounded repository metadata enrichment using the existing `httpx.Client`, timeout policy, user agent, and optional environment token without exposing credentials.
- [x] 2.2 Implement deterministic AI relevance filtering and Awesome/resource-list exclusion from public repository metadata.
- [x] 2.3 Implement canonical identity/URL deduplication, batch-relative popularity normalization, stable tie-breaking, and honest limit truncation.
- [x] 2.4 Add unit tests covering relevant and excluded projects, missing optional metadata, normalization, ties, insufficient results, and request bounds.

## 3. Pipeline Integration

- [x] 3.1 Convert selected Trending candidates into the existing raw-item contract with `github_trending` source and auditable structured source metrics.
- [x] 3.2 Add opt-in `github-trending` CLI parsing and pipeline dispatch while preserving existing `github` behavior and default source semantics.
- [x] 3.3 Add pipeline tests proving source selection, source-level failure isolation, raw persistence, downstream processing, and checkpoint compatibility.

## 4. Documentation and Validation

- [x] 4.1 Document GitHub Trending usage, weekly ranking semantics, optional `GITHUB_TOKEN`, limits, and the distinction from GitHub Search in `README.md` and `.env.example` where applicable.
- [x] 4.2 Run the full unit test suite and Ruff checks, fixing only regressions introduced by this change.
- [x] 4.3 Run formal article Schema validation and inspect `git diff` and `git status` to confirm no production data or unrelated user files changed.
