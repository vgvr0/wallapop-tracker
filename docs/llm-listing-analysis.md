# LLM listing analysis core

This module provides an isolated, provider-agnostic semantic analysis contract for one Wallapop listing. It does not make purchase decisions, does not replace deterministic `DealScore`, and treats LLM output as an untrusted structured signal.

## Architecture

`ListingAnalysisContext` is validated input. `ListingAnalyzer` is the async protocol. `OpenAICompatibleListingAnalyzer` is an HTTP-only adapter for OpenAI-style chat-completions APIs; it can be configured for compatible providers without coupling the domain to an SDK.

The result contains `ListingAIAnalysis` and `ListingAnalysisMetadata`. Defects and risk flags carry confidence and bounded evidence. Risk flags express signals, inconsistencies, or insufficient information; they must not accuse sellers of crimes.

## Scores and market context

`DealScore` remains deterministic and separate from `semantic_score`, which is LLM-derived. No combined score is calculated here. Market prices are only used when supplied in the context. The deterministic `discount_vs_market_median` helper is included in the prompt; the model must not invent a fair price.

## Prompts, failures, and retries

Prompts are versioned as `listing-analysis-v1` and keep system instructions separate from the JSON listing payload. HTTP timeout, transport errors, 429, and 5xx responses retry with bounded injectable backoff. 4xx errors other than 429 and schema/output failures do not retry. Provider exceptions are translated to `ListingAnalysisProviderError` or `ListingAnalysisValidationError`.

## Hashing and security

`build_analysis_input_hash` uses canonical JSON and SHA-256 over normalized context, provider, model, and prompt version. It is not persisted in this feature. API keys are constructor-only secrets: they are excluded from `repr`, logs, and exception messages. Full prompts, descriptions, and raw responses are not logged.

## Integration: persistence, cache, CLI, and API

The optional integration persists validated results in `listing_ai_assessments`. Its canonical cache identity is `(listing_id, provider, model, prompt_version, input_hash)`, protected by a database unique constraint. A cache hit never calls the provider. `force=true` calls the provider, but an identical identity remains a single canonical row; a changed input/model/prompt creates a separate historical row. A provider failure happens before persistence, so an earlier valid assessment remains available.

Configuration is disabled by default with `WALLAPOP_AI_ENABLED=false`. The provider is built lazily from `WALLAPOP_AI_*` variables when an explicit CLI/API assessment is requested. `wallapop-track ai assess <listing_id> [--force] [--json]` performs an explicit assessment; `wallapop-track ai show <listing_id>` is read-only. The REST endpoints are `POST /api/v1/listings/{listing_id}/ai-assessment?force=false` and read-only `GET /api/v1/listings/{listing_id}/ai-assessment`.

The integration records aggregate Prometheus counters and duration/token metrics without listing IDs, prompts, descriptions, raw responses, or secrets. Event bus integration is intentionally absent: tracking remains non-blocking and no mass or automatic analysis is scheduled.

## Limitations and future integration

The core has no database, event bus, worker, scheduler, notification, API, CLI, or persistence integration. A future worker can call the async analyzer in response to `listing.ai_analysis.requested` and publish a completed event without changing this contract.
