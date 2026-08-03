## 1. Weekly Storage

- [x] 1.1 Add a `knowledge/weekly/` storage path and a project-timezone filename helper that produces the 15-character `YYMMDD-HHMM-cai` stem.
- [x] 1.2 Implement dry-run-aware atomic weekly JSON persistence that stops on an existing target instead of overwriting it.
- [x] 1.3 Add storage tests for filename length and content, timezone conversion, valid JSON output, dry run, atomic replacement behavior, and collision rejection.

## 2. Container AI Collection

- [x] 2.1 Add typed constants and helpers for the seven-day UTC cutoff, bounded creation and push Search queries, shared GitHub headers, and the Top 15 source cap.
- [x] 2.2 Implement two required GitHub Search requests and merge their validated repository objects without allowing one failed query to produce a partial source batch.
- [x] 2.3 Implement token-aware dual-domain Container AI relevance checks and exclusions for archived, Awesome/resource-list, and tutorial-first repositories.
- [x] 2.4 Implement canonical identity and URL deduplication, current-total-Star ranking, URL tie-breaking, raw-item conversion, and auditable metric/window metadata.
- [x] 2.5 Add collector tests for both time windows, query bounds, relevance combinations, exclusions, malformed inputs, deduplication, ranking ties, Top 15 truncation, and undersized results.

## 3. Pipeline Integration

- [x] 3.1 Register the opt-in `container-ai` CLI source while preserving the current default source set and global limit behavior.
- [x] 3.2 Save a successful Container AI snapshot before adding its items to the common raw batch and downstream analysis workflow.
- [x] 3.3 Isolate Search and weekly-storage failures from RSS and other sources, prevent partial Container AI processing, and keep credentials out of failure data.
- [x] 3.4 Add pipeline tests for source selection, unchanged defaults, weekly-before-common persistence, dry run, checkpoint compatibility, and RSS continuation after each required failure.

## 4. Documentation and Validation

- [x] 4.1 Document Container AI scope, the created-or-pushed seven-day window, current-total-Star ranking, Top 15 cap, exclusions, weekly path and filename, and optional token behavior.
- [x] 4.2 Run the full unit test suite and Ruff checks, fixing only regressions introduced by this change.
- [x] 4.3 Run formal Article Schema validation and inspect `git diff` and `git status` to confirm no production weekly or article data and no unrelated user changes were modified.
