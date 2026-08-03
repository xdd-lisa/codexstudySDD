## Why

The knowledge pipeline has no focused way to discover recently created or active open-source projects for containerized AI training, inference, and deployment. A weekly Container AI source will provide a bounded, traceable project set while preserving the pipeline's existing analysis and Article Schema workflow.

## What Changes

- Add an opt-in `container-ai` CLI source backed only by GitHub's public API.
- Search separate seven-day creation and recent-push windows, merge their candidates, and rank qualifying repositories by current total Star count; the result MUST NOT be described as seven-day Star growth.
- Define Container AI as repositories directly related to containers, Kubernetes, or container runtimes used for AI model training, inference, or deployment.
- Exclude archived repositories, Awesome-style resource lists, and repositories whose primary purpose is a tutorial.
- Canonicalize and deduplicate repositories by lowercase `owner/repository`, return up to 15 results by default, and never pad an undersized result.
- Convert results to the existing raw-item contract and pass them through the current LLM analysis, failure isolation, checkpoint recovery, Article Schema validation, and formal article repository.
- Atomically save the weekly raw result under `knowledge/weekly/` with a date-bearing Container AI abbreviation; the filename stem MUST be at most 15 characters, excluding `.json`.

## Capabilities

### New Capabilities

- `weekly-container-ai-collection`: Discover, filter, rank, persist, and ingest weekly Container AI repository candidates from GitHub.

### Modified Capabilities

None.

## Impact

- Affected code: GitHub collection helpers in `pipeline/collector.py`, orchestration and CLI source selection in `pipeline/pipeline.py`, atomic weekly output support in `pipeline/storage.py`, and focused tests.
- Affected filesystem: a new `knowledge/weekly/` runtime-data directory containing bounded JSON batches; it remains separate from formal `knowledge/articles/`.
- External system: GitHub public Search and repository APIs using the existing `httpx` dependency, timeout policy, user agent, and optional environment token.
- Contracts: the existing raw-item shape and Article Schema v1 remain unchanged; no database, scheduler, new dependency, or data migration is introduced.
