# AAMP Buyer Agent — Campaign Briefs Synthetic Data

This directory contains pre-configured campaign briefs for the AAMP Buyer Agent.

## File Inventory

| File | Description |
|------|-------------|
| `campaign_briefs.json` | 3 campaign briefs covering automotive, retail, and entertainment verticals |

## Campaign Brief Inventory

| Brief ID | Vertical | Budget | Channels | Preferred Package |
|-----------|----------|--------|----------|-------------------|
| BRIEF-AUTO-SPORTS | Automotive | $500K | CTV, Linear | Max Premium Sports Bundle |
| BRIEF-RETAIL-NEWS | Retail | $250K | Linear, Digital Video, Display | CNN News Reach Package |
| BRIEF-ENT-STREAMING | Entertainment | $350K | CTV, Audio | Entertainment Upfront Package |

## Data Relationships

```
campaign_briefs.json
    │
    ├──> Seller Agent media_kits.json (preferred_package references)
    │
    ├──> Seller Agent inventory.csv (channel alignment)
    │
    └──> Seller Agent rate_card.json (target_cpm / max_cpm ranges)
```

## Key IDs

- `BRIEF-AUTO-SPORTS` — targets sports audiences on CTV, budget $500K
- `BRIEF-RETAIL-NEWS` — targets news audiences cross-platform, budget $250K
- `BRIEF-ENT-STREAMING` — targets streaming audiences on CTV+audio, budget $350K

## Usage Notes

1. Each brief includes: vertical, brand, budget, channels, target_audience, target_cpm, max_cpm, preferred_package, flight_dates
2. Briefs are aligned to the Seller Agent's Meridian Media Group-style inventory
3. `target_cpm` and `max_cpm` define the negotiation range for A2A deal negotiation
4. `preferred_package` references a media kit package ID from the seller agent's data
5. `channels` align to the seller agent's inventory types (ctv, linear, digital_video, display, audio)
