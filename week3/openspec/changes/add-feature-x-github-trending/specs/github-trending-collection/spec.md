## ADDED Requirements

### Requirement: Explicit GitHub Trending source
The system SHALL expose `github-trending` as a source distinct from the existing `github` source and SHALL process its results through the existing raw storage, analysis, failure isolation, checkpoint, and Article repository workflow.

#### Scenario: User selects GitHub Trending
- **WHEN** the user runs the pipeline with `--sources github-trending`
- **THEN** the system collects GitHub Trending candidates without invoking the general GitHub Search collector

#### Scenario: Existing GitHub source remains compatible
- **WHEN** the user runs the pipeline with `--sources github`
- **THEN** the system retains the existing general GitHub collection behavior

### Requirement: Traceable trending evidence
The collector SHALL derive rank and period popularity from a single GitHub Trending window and SHALL record the actual collection time, period, method, rank, period stars, and repository metadata in each raw item. The system MUST NOT describe GitHub Search results as GitHub's official Trending ranking.

#### Scenario: Weekly candidate has complete evidence
- **WHEN** a Trending entry provides repository identity and weekly star gain and repository metadata can be enriched
- **THEN** the raw item records weekly rank and star gain together with total stars, forks, description, language, topics, license, and update activity when supplied by GitHub

#### Scenario: Trending evidence is unavailable
- **WHEN** the Trending source cannot provide required repository identity or same-window period stars
- **THEN** the system reports a collection failure and does not substitute total stars or Search ordering as Trending evidence

### Requirement: Canonical repository identity and deduplication
The collector SHALL identify a repository by lowercase `owner/repository`, SHALL use a canonical `https://github.com/owner/repository` URL without tracking parameters or fragments, and SHALL emit at most one candidate for each identity or canonical URL.

#### Scenario: Duplicate repository entries
- **WHEN** the source contains multiple representations of the same repository with case differences or URL parameters
- **THEN** the collector emits one raw item with a stable `github:<lowercase-owner>/<lowercase-repository>` external identity

### Requirement: AI relevance and collection exclusions
The collector SHALL include only repositories whose public name, description, topics, or README summary directly evidence AI, LLM, agent, model training, inference, evaluation, or supporting infrastructure relevance. It SHALL exclude repositories primarily serving as Awesome lists, link indexes, or navigation collections.

#### Scenario: Directly relevant AI repository
- **WHEN** public repository metadata directly identifies a project as AI-domain software or infrastructure
- **THEN** the repository remains eligible for ranking

#### Scenario: Awesome resource list
- **WHEN** a repository is primarily an Awesome list, resource directory, link index, or navigation collection
- **THEN** the collector excludes it even if its metadata contains AI keywords

#### Scenario: Relevance is uncertain
- **WHEN** available public metadata does not directly establish AI-domain relevance
- **THEN** the collector excludes the repository rather than guessing or padding the result

### Requirement: Deterministic popularity and ordering
For each non-empty batch, the collector SHALL set `popularity_raw` and `source_metrics.period_stars` to the same period-star value, SHALL normalize popularity relative to the largest period-star value in that batch as an integer from 0 to 100, and SHALL sort by descending normalized popularity with canonical URL ascending as the tie-breaker.

#### Scenario: Batch contains different weekly gains
- **WHEN** eligible candidates have valid period-star values
- **THEN** the largest value receives popularity 100 and all candidates are ordered by normalized popularity

#### Scenario: Candidates tie on popularity
- **WHEN** two eligible candidates have equal normalized popularity
- **THEN** the candidate with the lexicographically smaller canonical URL appears first

### Requirement: Bounded honest results
The collector SHALL respect the pipeline's positive `--limit`, SHALL bound external requests, and SHALL return only qualifying repositories up to that limit. It MUST return fewer items when insufficient candidates qualify and MUST NOT fabricate or duplicate entries to fill the requested count.

#### Scenario: More candidates qualify than requested
- **WHEN** eligible candidates exceed the requested limit
- **THEN** the collector returns the first requested number after deterministic ranking

#### Scenario: Too few candidates qualify
- **WHEN** fewer eligible repositories are available than requested
- **THEN** the collector returns the available subset without padding

### Requirement: Safe public GitHub access
The collector SHALL use timeout-controlled public GitHub requests, SHALL send the project user agent, SHALL use `GITHUB_TOKEN` only when provided through the environment, and MUST NOT write credentials or authorization headers to raw data, failures, or logs.

#### Scenario: Optional token is configured
- **WHEN** `GITHUB_TOKEN` is present
- **THEN** GitHub API enrichment uses it for authorization without persisting or logging the token

#### Scenario: GitHub request fails
- **WHEN** a network, rate-limit, or invalid-response error prevents Trending collection
- **THEN** the pipeline isolates and records the source failure while allowing other selected sources to continue
