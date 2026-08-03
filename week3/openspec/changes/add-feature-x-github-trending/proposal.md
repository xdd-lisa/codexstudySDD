## Why

The current GitHub collector returns recently updated repositories matching a broad AI topic query, but it does not provide a reproducible “trending” window, popularity evidence, or the stricter AI relevance and exclusion rules needed to identify noteworthy projects. Adding an explicit GitHub Trending collection mode lets the pipeline ingest traceable, ranked candidates without presenting GitHub Search results as an official GitHub Trending ranking.

## What Changes

- Add a GitHub Trending collector that gathers public repository candidates for a defined time window and records the query, collection time, and popularity evidence.
- Filter candidates to projects directly related to AI, LLMs, agents, training, inference, evaluation, or supporting infrastructure, while excluding Awesome-style resource lists and link indexes.
- Normalize and deduplicate repositories by lowercase `owner/repository` and canonical GitHub URL.
- Rank a bounded batch deterministically using same-window popularity data, with stable URL tie-breaking, and return fewer results rather than padding when too few repositories qualify.
- Convert selected repositories into the existing pipeline raw-item contract so downstream LLM analysis, failure isolation, checkpoint recovery, Article Schema validation, and repository storage remain shared.
- Expose the new source through the existing CLI source selection and document its semantics, limits, and authentication behavior.

## Capabilities

### New Capabilities

- `github-trending-collection`: Collect, filter, rank, and normalize traceable GitHub Trending candidates for ingestion by the existing knowledge pipeline.

### Modified Capabilities

None.

## Impact

- Affected code: `pipeline/collector.py`, `pipeline/pipeline.py`, CLI source parsing, and focused tests in `tests/test_pipeline.py`.
- Affected documentation/configuration: `README.md` and `.env.example` if token behavior needs clarification.
- External system: public GitHub APIs/pages, using the existing `httpx` dependency, request timeout, optional `GITHUB_TOKEN`, and source-level failure isolation.
- Persistent data: raw batches and formal articles continue to use existing storage and Article Schema contracts; no database, cache, or schema migration is introduced.
