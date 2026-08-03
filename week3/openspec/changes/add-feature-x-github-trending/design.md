## Context

The pipeline currently exposes `github` and `rss` sources. `collect_github()` queries GitHub Search for repositories matching a broad AI topic and sorts by `updated`, then converts results directly into the shared raw-item shape. The pipeline already supplies bounded collection, HTTP timeouts, source-level failure isolation, raw-batch persistence, LLM analysis, checkpoint recovery, and formal Article validation.

GitHub does not expose an official Trending API. Search API star totals or recently updated results are not equivalent to the period-specific star gains displayed by GitHub Trending. The collector therefore needs an explicit provenance model and must not overstate Search-derived results as an official ranking.

## Goals / Non-Goals

**Goals:**

- Add a bounded `github-trending` source while retaining the existing `github` source behavior.
- Use GitHub Trending as the source of period rank and period-star evidence, then enrich candidates from public repository metadata.
- Produce existing pipeline raw items with stable identities and enough source metadata for auditability.
- Apply deterministic AI relevance filtering, Awesome-list exclusion, canonicalization, deduplication, normalization, and ordering.
- Preserve current timeout, optional token, failure isolation, resume, storage, and Article Schema behavior.

**Non-Goals:**

- Claiming that GitHub Search results are GitHub's official Trending list.
- Changing Article Schema v1 or persisting all popularity fields in formal articles.
- Adding a database, cache, browser automation, HTML parsing framework, or background scheduler.
- Computing exact historical star deltas when GitHub does not expose them.
- Replacing the existing general `github` collector.

## Decisions

### Use a distinct CLI source

Add `github-trending` to the supported source names and dispatch it independently from `github`. This keeps existing behavior backward-compatible and makes the different ranking semantics visible to callers.

Alternative considered: change `github` in place. Rejected because it silently changes established collection and checkpoint behavior.

### Treat the Trending page as ranking evidence and the API as enrichment

Request the public weekly GitHub Trending page, extract repository identity, displayed period stars, and page rank, and then hydrate eligible repositories through the GitHub repository API. GitHub API requests reuse the existing user agent, optional `GITHUB_TOKEN`, and timeout-controlled `httpx.Client`.

If Trending markup is missing required identity or period-star data, the collector fails that source with a clear validation error instead of silently falling back to Search and calling it Trending. Repository enrichment failures are isolated per candidate where possible; a complete inability to read the Trending source is a source-level failure.

Alternative considered: GitHub Search with a recent creation/update query. Rejected as the primary ranking input because total stars and updated timestamps do not measure stars gained in a shared window.

### Keep the shared raw contract and attach auditable source metadata

Each result retains the fields consumed by the pipeline: `external_id`, `title`, `source`, `source_url`, `published_at`, `collected_at`, `content`, and `source_tags`. The source is `github_trending` so formal Article values remain schema-compatible.

Raw items additionally carry structured `source_metrics` containing rank, weekly period stars, total stars, forks, language, topics, license, description, update/push times, and the collection method. These fields remain in raw batches for traceability; `normalize_article()` continues to construct only Article Schema fields.

### Filter deterministically before ranking

Canonical identity is lowercase `owner/repository`; canonical URL is `https://github.com/<owner>/<repository>` with query and fragment removed. Duplicates are removed by either identity or URL.

Relevance is determined from repository name, description, topics, and README summary using explicit AI-domain terms. Repositories whose name, topics, or primary README purpose identify them as Awesome/resource/link collections are excluded. Missing optional metadata is represented by `None` or empty collections and is never inferred.

Alternative considered: ask the LLM to filter candidates. Rejected because collection must remain deterministic, testable, and free of model cost before raw persistence.

### Normalize popularity within one batch

`popularity_raw` equals the displayed weekly stars and matches `source_metrics.period_stars`. For a non-empty batch, `popularity` is the period-star count divided by the batch maximum and rounded to an integer from 0 to 100. Results sort by descending popularity and canonical URL ascending on ties, then truncate to the requested limit. The collector returns fewer items if too few candidates qualify.

### Keep parsing small and test from fixtures

Implement narrow parsing helpers in `pipeline/collector.py` using the standard library, with fixture strings representing relevant Trending markup and API payloads. Parsing validates required fields and limits input sizes. This avoids a new dependency while keeping the externally unstable markup isolated behind focused tests.

## Risks / Trade-offs

- [GitHub changes Trending HTML] → Isolate markup parsing, validate required fields, cover representative fixtures, and report a source-level failure rather than emitting misleading data.
- [Unauthenticated API rate limits prevent full enrichment] → Honor `GITHUB_TOKEN`, bound requests by `--limit`, expose actionable errors, and retain source failure isolation.
- [Keyword filtering produces false positives or negatives] → Keep rules explicit and tested; prefer excluding uncertain projects rather than padding the result.
- [Weekly period stars are unavailable for a candidate] → Exclude that candidate because cross-window or total-star substitutions would invalidate ranking semantics.
- [Extra raw metadata is not promoted to Article Schema] → Preserve it in raw batches for auditability and defer a schema change until a separate requirement justifies it.
- [One API request per candidate increases latency] → Cap candidate hydration and stop once enough qualifying results plus a small bounded buffer have been examined; do not introduce concurrency without measured need.

## Migration Plan

1. Add parsing, normalization, filtering, enrichment, and ranking helpers with unit tests.
2. Add the new source to pipeline dispatch and CLI validation without changing existing source defaults unless explicitly documented.
3. Update README usage and semantics.
4. Run unit tests, Ruff, and formal article validation.

Rollback consists of removing the new source dispatch and helpers. Existing checkpoints and articles remain valid because no schema or storage migration is performed.

## Open Questions

- Whether `github-trending` should eventually become a default source should be decided from reliability and rate-limit observations; this change keeps opt-in behavior.
- If GitHub stops publishing period-star counts, a separately named Search-based discovery source can be proposed rather than changing Trending semantics.
