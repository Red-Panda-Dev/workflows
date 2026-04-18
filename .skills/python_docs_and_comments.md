---
description: "Comment and docstring policy for Python aiohttp + SQLAlchemy backends. AI agents must write concise, high-signal comments and Google-style docstrings for public APIs and HTTP handlers."
globs:
  - "**/*.py"
alwaysApply: true
---

## Comment Guidelines

- **Do not add obvious comments**: avoid comments that merely restate the code.
- **Prefer self-documenting code**: use clear names, small functions, and extracted helpers before adding comments.
- **Comment the why, not the what**: explain intent, invariants, trade-offs, failure modes, and business rules.
- **Prefer refactoring over narration**: if the code is hard to explain, simplify it before adding comments.
- **Keep comments durable**: avoid comments that are likely to drift as implementation details change.
- **Do not lie in comments**: if behavior is inferred or uncertain, rewrite the code or omit the comment.
- **Keep comments short**: one or two sentences is usually enough.

## When comments are appropriate

Add comments only when they clarify something non-obvious, such as:

- business rules or financial/domain formulas
- unusual validation rules
- security-sensitive behavior
- transaction boundaries
- concurrency assumptions
- retry/backoff behavior
- performance-sensitive code
- external API quirks or protocol constraints
- reasons a simpler-looking approach was intentionally not used

## When comments are not needed

Do not comment:

- straightforward assignments, loops, and conditionals
- obvious conversions or mappings
- names that already communicate intent
- framework boilerplate unless there is a local gotcha
- private helpers that are trivial and local

## TODO / FIXME policy

- Use `TODO:` only for concrete, actionable follow-up work.
- Use `FIXME:` only for known incorrect or fragile behavior that should be corrected.
- Include brief context so future readers understand the risk or missing work.
- Do not leave vague notes like `TODO: improve this`.

Examples:

- `# TODO: Support partial issuer updates once upstream API exposes revision IDs.`
- `# FIXME: This fallback treats missing coupon dates as current month, which can skew forecasts.`

## Docstring Policy

### Required style

- Use **Google-style docstrings**.
- Follow **PEP 257** conventions for summary line, blank line, and general structure.
- Keep docstrings consistent and machine-readable.

### Docstrings are required for

- public modules with externally relevant API or behavior
- public classes
- public functions and methods
- aiohttp route handlers and other HTTP endpoint entrypoints
- non-trivial async workflows or orchestration functions with side effects

### Docstrings are optional for

- private helpers
- tiny local utilities
- obvious test helpers
- trivial wrappers with no independent behavior

Add docstrings to these only when the behavior, contract, or side effects are not obvious.

### Do not add docstrings that provide no value

Avoid docstrings that simply paraphrase the function name or repeat the type hints.

Bad:

```python
def get_user(user_id: int) -> User:
    """Get user."""
````

Good:

```python
def get_user(user_id: int) -> User:
    """
    Return an active user by internal identifier.

    Raises an error if the user does not exist or has been soft-deleted.
    """
```

## Docstring content rules

### General rules

Docstrings should describe:

* purpose
* important inputs and outputs
* behavior relevant to callers
* side effects
* raised exceptions
* important invariants or constraints

Docstrings should not describe:

* step-by-step implementation details
* obvious control flow
* internal temporary variables
* details already fully expressed by simple code and type hints

### Google-style sections

Use relevant sections when applicable:

* `Args`
* `Returns`
* `Raises`
* `Yields`
* `Attributes`

Do not force sections that do not apply.

### Accuracy rule

Only document facts that are supported by the code.

* If route path is not directly visible, omit it.
* If exact status codes are not clear, describe the response conservatively.
* If security behavior is uncertain, do not invent it.
* If a function may return `None`, document that explicitly.

## aiohttp Handler Docstrings

All aiohttp HTTP handlers should have docstrings.

Handler docstrings should describe, when known from the code:

* purpose of the endpoint
* HTTP method
* route path
* expected request body or content type
* important path/query parameters
* success response shape or model
* important failure cases
* status codes
* authentication/authorization expectations
* side effects
* transactional behavior
* idempotency expectations if relevant

Do not invent request/response details that are not observable.

### Handler example

```python
from aiohttp import web

async def create_user_handler(request: web.Request) -> web.Response:
    """
    Create a new user.

    Handles HTTP POST requests for user creation and returns the created entity.

    Args:
        request (web.Request): Incoming request with JSON body matching the user creation schema.

    Returns:
        web.Response: JSON response containing the created user payload.

    Raises:
        web.HTTPBadRequest: If the request body is invalid.
        web.HTTPConflict: If a user with the same unique field already exists.
    """
    ...
```

## Async and Side-Effectful Code

For async functions, background jobs, DB-writing operations, and integration calls, document the parts that matter to callers:

* whether the function performs IO
* whether it mutates database state
* whether it calls external services
* whether failures are retried, swallowed, or propagated
* whether ordering, locking, or transactional assumptions matter

Example:

```python
async def sync_issuer_profile(issuer_id: int) -> None:
    """
    Synchronize issuer profile data from the upstream provider.

    Fetches external data, normalizes it, and updates the local persisted profile
    within a single transaction.

    Args:
        issuer_id (int): Internal issuer identifier.

    Raises:
        UpstreamServiceError: If the provider request fails.
        DataNormalizationError: If required upstream fields are missing.
    """
```

## Complex logic

Add comments or docstring notes for non-obvious logic such as:

* pricing and yield formulas
* payout schedule calculations
* calendar/date conventions
* rounding rules
* fallback or reconciliation logic
* caching behavior
* deduplication rules
* heuristics
* algorithmic complexity when relevant

Document:

* the intent
* the domain reason
* the critical invariant
* the trade-off, if one exists

## Module docstrings

Use module docstrings only when the module has meaningful public role or architectural significance.

Good candidates:

* API modules
* service modules
* integration clients
* domain logic modules
* background job modules

A module docstring should briefly explain:

* what this module owns
* what kind of responsibilities belong here
* important boundaries or invariants

## SQLAlchemy / persistence notes

For persistence-facing code, document behavior that can surprise callers, such as:

* flush vs commit assumptions
* lazy/eager loading expectations
* locking semantics
* soft-delete filtering
* uniqueness assumptions
* transaction ownership

Do not document generic ORM behavior unless the local code relies on a non-obvious convention.

## Bad vs Good comments

* **Bad**: `# increment i` for `i += 1`
* **Bad**: `# fetch users from database` immediately above a clearly named query helper
* **Good**: `# Use issuer registry ID as the stable join key because ticker symbols can be reused after reorganization.`
* **Good**: `# Keep this calculation aligned with ACT/ACT ISDA rules; changing rounding order will affect coupon totals.`

## Enforcement notes

* Keep docstrings machine-parsable and structurally consistent.
* Prefer updating stale comments/docstrings over adding new nearby commentary.
* If code changes invalidate a comment or docstring, update or remove it in the same edit.
* Public API and handler docstrings should be treated as part of the contract with future readers and tools.
