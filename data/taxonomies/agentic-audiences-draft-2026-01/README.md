# IAB Agentic Audiences (DRAFT, 2026-01)

Vendored subset of the IAB Tech Lab Agentic Audiences specification, used by
the buyer's Audience Planner agent for resolving Agentic audience references
(embedding-based dynamic audiences).

## Source

- **Upstream:** https://github.com/IABTechLab/agentic-audiences
- **Version:** draft-2026-01 (last upstream update 2026-01-28)
- **Status:** DRAFT
- **Fetched at:** 2026-04-25T19:27:21Z

## What is vendored

Only the subset relevant to grounding the wire format used by the buyer:

```
spec/
  README.md                                Project overview + naming history (UCP -> Agentic Audiences)
  LICENSE                                  CC-BY 4.0 (spec text)
  LICENSE-APACHE                           Apache-2.0 (reference implementations)
  specs/
    roadmap.md                             Spec roadmap (placeholder upstream as of fetch)
    v1.0/
      embedding-exchange.md                Wire format for embedding exchange
      embedding-taxonomy.md                Embedding taxonomy / signal types
      examples/
        buyer_agent_request.json           Example buyer-side payload (placeholder upstream)
        embedding_update.json              Example embedding update payload
        seller_agent_response.json         Example seller-side response (placeholder upstream)
      schema/
        agent_interface.schema.json        Agent interface JSON Schema (placeholder upstream)
        embedding_format.schema.json       Embedding format JSON Schema
```

Files marked "placeholder upstream" are 0 bytes in the source repository at
the time of vendoring. They are kept as empty files here to preserve the
spec layout; they will be populated when the upstream repo fills them in.

The full upstream repo also contains `prebid-module/`, `src/`, `community/`,
and `catalog-info.yaml` -- those are reference implementations and not
required to ground the wire format, so they are not vendored.

## License

The Agentic Audiences project ships under **two** licenses:

- **CC-BY 4.0** for the specification text (`spec/LICENSE`).
- **Apache 2.0** for reference implementations (`spec/LICENSE-APACHE`).

> Copyright (c) 2025 LiveRamp Holdings, Inc.
> Spec content distributed under CC-BY 4.0.
> Reference implementations distributed under Apache-2.0.

This vendored copy is unmodified. Any downstream use must preserve both
attributions where applicable.

## Naming note

Agentic Audiences was previously known as the **User Context Protocol (UCP)**.
The buyer's `ucp_*` modules implement this spec; see
`docs/proposals/AUDIENCE_PLANNER_3TYPE_EXTENSION_2026-04-25.md` Section 5.6
for the rename rationale.

## Update process

This subset is vendored, not fetched at runtime. To upgrade:

1. Re-fetch the files listed above from the upstream repo.
2. Recompute the composite hash recorded in
   `data/taxonomies/taxonomies.lock.json` under the `agentic` key.
3. Update the `Fetched at` timestamp here and the `version` field in
   the lock file (e.g., `draft-2026-01` -> `draft-2026-04`).
4. Re-run any wire-format validation tests; the spec is DRAFT and shapes
   may change between fetches.
