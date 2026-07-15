# IAB Audience Taxonomy 1.1

Vendored copy of the IAB Tech Lab Audience Taxonomy 1.1 used by the buyer's
Audience Planner agent for resolving Standard audience references.

## Source

- **Upstream:** https://github.com/InteractiveAdvertisingBureau/Taxonomies
- **Raw URL:** https://raw.githubusercontent.com/InteractiveAdvertisingBureau/Taxonomies/main/Audience%20Taxonomies/Audience%20Taxonomy%201.1.tsv
- **Version:** 1.1
- **Format:** Tab-separated values (TSV)
- **Fetched at:** 2026-04-25T19:27:21Z

## Tier 1 categories

The taxonomy splits into three Tier 1 buckets:

- Demographic
- Interest-based
- Purchase-intent

See `Audience Taxonomy 1.1.tsv` for the complete tier hierarchy.

## License

Released under **Creative Commons Attribution 3.0 Unported** (CC-BY 3.0).

> Copyright (c) IAB Tech Lab. Distributed under CC-BY 3.0.
> https://creativecommons.org/licenses/by/3.0/

This vendored copy is unmodified. Any downstream use must preserve attribution
to the IAB Tech Lab.

## Update process

This file is vendored, not fetched at runtime. To upgrade:

1. Fetch the new TSV from the source URL.
2. Recompute its sha256.
3. Update both `data/taxonomies/taxonomies.lock.json` and the
   `Fetched at` timestamp in this README.
4. Land the change behind a code review that includes any required
   migration logic for deleted/renamed segment IDs.

The integrity hash for the currently-vendored file lives in
`data/taxonomies/taxonomies.lock.json` under the `audience` key.
