## Purpose

Define the observable behavior for discovering, filtering, ranking, persisting, and ingesting weekly Container AI repository candidates from GitHub.

## Requirements

### Requirement: Explicit Container AI source
The system SHALL expose `container-ai` as an opt-in CLI source and SHALL leave the existing default source set unchanged. It SHALL process selected repositories through the existing raw-item analysis, failure isolation, checkpoint, Article Schema, and repository workflow.

#### Scenario: User selects Container AI
- **WHEN** the user runs the pipeline with `--sources container-ai`
- **THEN** the system invokes Container AI collection and downstream processing

#### Scenario: User does not select Container AI
- **WHEN** the user runs the CLI without specifying sources
- **THEN** the default source set remains unchanged and Container AI collection is not invoked

### Requirement: Seven-day creation or activity window
At collection start, the system SHALL compute a UTC cutoff seven days before the collection timestamp and SHALL search GitHub's public API separately for repositories created at or after the cutoff and repositories pushed at or after the cutoff.

#### Scenario: Repository is newly created
- **WHEN** a repository was created within the seven-day window and satisfies all eligibility rules
- **THEN** it remains eligible even if it appears only in the creation search

#### Scenario: Existing repository is recently active
- **WHEN** a repository was pushed within the seven-day window and satisfies all eligibility rules
- **THEN** it remains eligible even if it was created before the window

#### Scenario: Repository is outside both windows
- **WHEN** a repository was neither created nor pushed within the seven-day window
- **THEN** the system excludes it

### Requirement: Container AI relevance
The system SHALL include only repositories whose public name, description, or topics directly evidence both a container, Kubernetes, or container-runtime concern and an AI model training, inference, serving, deployment, or acceleration concern.

#### Scenario: Dual-domain project
- **WHEN** repository metadata contains direct container-domain and AI-lifecycle evidence
- **THEN** the repository remains eligible

#### Scenario: Container-only project
- **WHEN** repository metadata evidences containers but no AI lifecycle use
- **THEN** the repository is excluded

#### Scenario: AI-only project
- **WHEN** repository metadata evidences AI but no container or orchestration use
- **THEN** the repository is excluded

### Requirement: Repository exclusions
The system SHALL exclude archived repositories, Awesome/resource-list repositories, and repositories whose primary purpose is a tutorial. Uncertain metadata MUST NOT be supplemented with guessed functionality.

#### Scenario: Archived repository
- **WHEN** GitHub metadata sets `archived` to true
- **THEN** the repository is excluded

#### Scenario: Resource collection or tutorial
- **WHEN** the repository name, description, or topics identify an Awesome list, resource index, or tutorial-first project
- **THEN** the repository is excluded

### Requirement: Canonical deduplication
The system SHALL canonicalize identity as lowercase `owner/repository`, SHALL use a canonical GitHub repository URL without query or fragment, and SHALL emit each repository at most once across the creation and push searches.

#### Scenario: Repository appears in both searches
- **WHEN** case or URL variants from both searches identify the same repository
- **THEN** the merged result contains one raw item with a stable `github:<lowercase-owner>/<lowercase-repository>` external identity

### Requirement: Current-Star ranking and bounded results
The system SHALL rank eligible repositories by descending current total `stargazers_count`, SHALL use canonical URL ascending as the tie-breaker, and SHALL return at most the requested limit. The Container AI source's documented default result count SHALL be 15, and the system MUST NOT fabricate or duplicate entries when fewer qualify.

#### Scenario: More than fifteen projects qualify under the default
- **WHEN** the source uses its default count and more than fifteen eligible repositories are found
- **THEN** the system returns the first fifteen after deterministic ranking

#### Scenario: Projects tie on current Stars
- **WHEN** eligible repositories have equal current total Stars
- **THEN** the lexicographically smaller canonical URL appears first

#### Scenario: Fewer projects qualify
- **WHEN** fewer repositories qualify than the requested count
- **THEN** the system returns the available subset without padding

### Requirement: Honest popularity semantics
The system SHALL record the current total Star count, seven-day cutoff, collection timestamp, creation and push query methods, and relevant repository timestamps in raw metadata. It MUST NOT represent current total Stars as Stars gained during the seven-day window.

#### Scenario: Raw result is inspected
- **WHEN** a caller reads a Container AI raw item
- **THEN** its metric is identified as `current_total_stars` and its seven-day window is identified as a candidate activity window

### Requirement: Weekly snapshot
Before Container AI items enter common downstream processing, the system SHALL atomically save the complete selected raw-item array under `knowledge/weekly/` using the filename format `YYMMDD-HHMM-cai.json`. The filename stem SHALL contain the project-local collection date/time and Container AI abbreviation and MUST NOT exceed 15 characters; `.json` is excluded from this limit.

#### Scenario: Weekly snapshot is saved
- **WHEN** Container AI collection succeeds
- **THEN** one valid JSON array is atomically written under `knowledge/weekly/` with a 15-character `YYMMDD-HHMM-cai` stem

#### Scenario: Weekly filename conflicts
- **WHEN** the target weekly filename already exists
- **THEN** the system reports a storage conflict and does not overwrite the existing file

#### Scenario: Dry run
- **WHEN** the pipeline runs in dry-run mode
- **THEN** no weekly snapshot is written

### Requirement: Source failure isolation
The system SHALL use timeout-controlled GitHub requests with the project user agent and optional environment token. A required Search request or weekly snapshot failure SHALL fail the Container AI source without exposing credentials or passing a partial Container AI batch downstream, while other selected sources continue.

#### Scenario: One GitHub Search request fails
- **WHEN** either the creation or push Search request fails and RSS is also selected
- **THEN** the system records the Container AI source failure and continues RSS collection

#### Scenario: Weekly persistence fails
- **WHEN** the weekly snapshot cannot be atomically saved
- **THEN** the system records the Container AI source failure and does not pass its partial items to analysis

#### Scenario: Optional token is configured
- **WHEN** `GITHUB_TOKEN` is present
- **THEN** GitHub API requests use it without writing the token or authorization header to output, failures, or logs
