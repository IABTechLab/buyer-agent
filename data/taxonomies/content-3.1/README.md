# IAB Content Taxonomy 3.1

Vendored copy of the IAB Tech Lab Content Taxonomy 3.1 used by the buyer's
Audience Planner agent for resolving Contextual audience references.

## Source

- **Upstream:** https://github.com/InteractiveAdvertisingBureau/Taxonomies
- **Raw URL:** https://raw.githubusercontent.com/InteractiveAdvertisingBureau/Taxonomies/main/Content%20Taxonomies/Content%20Taxonomy%203.1.tsv
- **Version:** 3.1
- **Format:** Tab-separated values (TSV)
- **Fetched at:** 2026-04-25T19:27:21Z

## Notes

- ~1,500 hierarchical categories across 4 tiers.
- **Non-backwards compatible** with 2.x: deletions exist between major versions.
  The IAB ships an "IAB Mapper" tool for migrating 2.x → 3.x IDs.
- Cross-mapped to CTV Genre, Podcast Genre, and Ad Product taxonomies.
- OpenRTB 2.6 enum value for this taxonomy version: `cattax = 7`.

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
4. Run any required migration over briefs/campaigns that reference deleted
   IDs (see "Non-backwards compatible" note above).

The integrity hash for the currently-vendored file lives in
`data/taxonomies/taxonomies.lock.json` under the `content` key.
