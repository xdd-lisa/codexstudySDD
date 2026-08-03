## Context

The pipeline currently supports general GitHub, GitHub Trending, and RSS collection before passing raw items through common analysis, checkpoint, and Article repository stages. Generic raw batches are written under `knowledge/raw/`; this change also requires a topic-specific weekly snapshot under `knowledge/weekly/`.

GitHub Search exposes current `stargazers_count` and supports creation and push-time qualifiers, but it does not provide a global “seven-day Star growth” ranking. The agreed metric is therefore a seven-day activity window ranked by current total Stars.

## Goals / Non-Goals

**Goals:**

- Add an opt-in `container-ai` source with a default result limit of 15.
- Discover repositories created or pushed during the previous seven days and rank the merged candidate set by current total Stars.
- Require evidence from both the container domain and the AI model lifecycle domain.
- Exclude archived repositories, Awesome/resource lists, and tutorial-first repositories.
- Persist a dedicated weekly snapshot safely and continue through the existing raw-item processing workflow.
- Keep source failures isolated from RSS and other selected sources.

**Non-Goals:**

- Computing or claiming seven-day Star growth.
- Adding a scheduler or changing the global CLI `--limit` default.
- Changing Article Schema v1 or the formal Article filename contract.
- Adding a database, index, cache, third-party parser, or GitHub SDK.

## Decisions

### Query two explicit seven-day windows

At collection start, compute a UTC cutoff exactly seven days before `collected_at`. Issue bounded GitHub Search requests for Container AI terms with `created:>=<cutoff>` and `pushed:>=<cutoff>`, each sorted by total Stars descending. Merge both result sets before filtering and final ranking.

Two queries make the “created or updated” requirement explicit and testable. GitHub's `pushed` qualifier is used as the observable repository-activity signal; repository metadata-only edits are not treated as code activity.

Alternative considered: one broad query followed by local timestamp filtering. Rejected because it can omit qualifying repositories before local filtering.

### Require dual-domain relevance

A repository qualifies only when its name, description, or topics provide at least one container signal (`container`, `kubernetes`, `k8s`, `docker`, `oci`, `containerd`, `cri`) and at least one AI lifecycle signal (`ai`, `ml`, `llm`, `model training`, `inference`, `serving`, `gpu`, `accelerator`). Token-aware matching prevents incidental substrings from qualifying a project.

Archived repositories are rejected using GitHub's `archived` field. Awesome/resource collections and tutorial-first repositories are rejected through explicit name, description, and topic markers. Uncertain repositories are excluded rather than inferred.

Alternative considered: LLM filtering. Rejected because collection must remain deterministic, testable, and free from analysis cost before raw persistence.

### Rank current Stars with deterministic ties

After canonicalizing lowercase `owner/repository` identities and URLs, deduplicate across both searches. Sort eligible repositories by descending current `stargazers_count`, then canonical URL ascending. The source caps its output at `min(allocated_limit, 15)`, so selecting only `container-ai` with the unchanged global default returns Top 15 while smaller user or mixed-source allocations remain bounded. Return fewer items when fewer qualify.

Raw metadata records the cutoff, both query methods, total Stars, relevant timestamps, and the explicit metric name `current_total_stars`. It MUST NOT label the metric as period Star growth.

### Reuse the raw-item contract

Each result provides `external_id`, `title`, `source`, `source_url`, `published_at`, `collected_at`, `content`, and `source_tags`; `source` is `container_ai`. Additional search evidence remains in raw-only `source_metrics`. Downstream Article normalization continues to construct only Article Schema fields.

The source-specific weekly snapshot is saved before its items are added to the common collected batch. The common batch may still be written under `knowledge/raw/`, preserving current audit and recovery behavior.

### Use a short, collision-safe weekly filename

Add weekly storage behind `pipeline/storage.py`, reusing `write_json_atomic()`. The filename format is:

```text
YYMMDD-HHMM-cai.json
```

The stem is exactly 15 characters, contains local collection date/time and the `cai` Container AI abbreviation, and excludes `.json` from the length calculation. Project timezone (`Asia/Shanghai`) determines the filename time; the JSON `collected_at` remains a timezone-aware ISO 8601 value.

If a file with the same minute name already exists, storage MUST stop with a conflict rather than overwrite it. This preserves the filename bound and the project's no-overwrite rule.

Alternative considered: sequence suffixes. Rejected because unbounded suffix growth can violate the 15-character stem limit.

### Keep failures isolated at source boundaries

Failure of either required Search request or weekly snapshot persistence marks `container-ai` collection as failed and prevents partial Container AI items from entering downstream processing. Other selected sources continue. Invalid individual repository objects are skipped without fabricating replacements.

## Risks / Trade-offs

- [Search uses current total Stars, not weekly Star gain] → Record and document the metric explicitly in raw metadata and CLI documentation.
- [Keyword filtering misses novel terminology] → Keep term sets centralized and fixture-tested; update them only with concrete examples.
- [Two Search requests consume rate limit] → Reuse optional `GITHUB_TOKEN`, cap pages and result counts, and honor the shared timeout.
- [Push activity is not every kind of repository update] → Document `pushed_at` as the operational meaning of “updated”.
- [Two runs in one minute collide] → Stop without overwriting and report the conflicting path so the caller can retry in a later minute.
- [Weekly and generic raw storage duplicate the selected items] → Accept bounded duplication because the weekly artifact serves topic delivery while the generic batch preserves existing pipeline audit behavior.

## Migration Plan

1. Add weekly path generation and atomic, no-overwrite storage tests.
2. Add Container AI query, merge, filtering, deduplication, and ranking tests.
3. Register the opt-in source and save its weekly snapshot before common processing.
4. Document the metric, time window, output path, filename convention, and token behavior.
5. Run unit tests, Ruff, and formal article validation.

Rollback removes the source dispatch and weekly writer. Existing weekly JSON files remain inert runtime data and formal articles remain Schema-compatible.

## Open Questions

None.
