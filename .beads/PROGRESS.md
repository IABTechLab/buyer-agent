# Buyer Agent V2 — Progress

**39 open** | **0 in progress** | **51 closed** | **9 blocked** | 90 total

`[███████████░░░░░░░░░] 57% (51/90)`

## Phase 1 — Seller Interoperability

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[x] | buyer-jin | 1A: API Key Authentication Client | P1 | — | 2026-03-08 |
| \[x] | buyer-f8l | 1B: Agent Registry Discovery Client | P1 | — | 2026-03-08 |
| \[x] | buyer-ep2 | 1C: Tiered Identity Presentation Strategy | P1 | — | 2026-03-08 |
| \[x] | buyer-8rb | 1D: Media Kit Discovery Client | P1 | — | 2026-03-08 |
| \[x] | buyer-llu | 1E: Multi-Turn Negotiation Client | P1 | — | 2026-03-09 |
| \[x] | buyer-1ku | 1F: Session Persistence Client | P1 | — | 2026-03-08 |
| \[x] | buyer-6io | 1G: Linear TV Buying Support | P2 | — | 2026-03-11 |
| \[x] | buyer-hu7 | 1H: IAB Deals API v1.0 Client | P1 | — | 2026-03-10 |
| \[x] | buyer-5er | 1I: Order State Machine Implementation | P1 | — | 2026-03-11 |
| \[x] | buyer-cjx | 1J: Event Bus Implementation | P1 | — | 2026-03-11 |

## Phase 2 — Campaign Automation

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[x] | buyer-8ih | 2A: Multi-Seller Deal Orchestration | P2 | — | 2026-03-19 |
| \[x] | buyer-u8l | 2B: Campaign Brief to Deal Pipeline | P2 | — | 2026-03-19 |
| \[x] | buyer-9zz | 2C: Budget Pacing & Reallocation | P2 | — | 2026-03-19 |
| \[x] | buyer-3aa | 2D: Creative Management Sub-Agent | P2 | — | 2026-03-19 |
| \[x] | buyer-7m8 | 2E: Innovid & Flashtalking Creative Integration | P2 | — | 2026-03-19 |
| \[x] | buyer-uoz | Ad server integration record storage | P1 | — | 2026-03-19 |
| \[x] | buyer-78z | Add READY state to campaign state machine | P2 | — | 2026-03-19 |
| \[x] | buyer-80k | Campaign brief JSON schema | P1 | — | 2026-03-19 |
| \[x] | buyer-80o | Campaign data model (schema) | P1 | — | 2026-03-19 |
| \[x] | buyer-ppi | Campaign event types | P1 | — | 2026-03-19 |
| \[x] | buyer-f58 | Campaign reporting tools | P1 | — | 2026-03-19 |
| \[x] | buyer-0u9 | Campaign state machine | P1 | — | 2026-03-19 |
| \[x] | buyer-89g | Creative asset storage | P1 | — | 2026-03-19 |
| \[x] | buyer-gb2 | Cross-track integration test | P1 | — | 2026-03-19 |
| \[x] | buyer-2qs | Human approval gates / event bus | P1 | — | 2026-03-19 |
| \[x] | buyer-lna | Pacing snapshot storage | P1 | — | 2026-03-19 |
| \[x] | buyer-lae | Quote normalization logic | P1 | — | 2026-03-19 |

## Phase 3 — Platform & Infrastructure

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[!] | buyer-4bg | 3A: FreeWheel Buyer Cloud Integration | P2 | seller-dcd |  |
| \[!] | buyer-kyo | 3B: Order Lifecycle State Machine | P2 | seller-awh |  |
| \[ ] | buyer-zzq | 3C: API & SDK Documentation | P3 | — |  |
| \[ ] | buyer-je8 | 3D: Mediaocean Order Management Integration | P2 | — |  |
| \[!] | buyer-k2i | &nbsp;&nbsp;↳ 3D-Phase1: Mediaocean Prisma (Digital/Programmatic) | P2 | buyer-je8 |  |
| \[!] | buyer-wwf | &nbsp;&nbsp;↳ 3D-Phase2: Mediaocean Lumina (Linear TV) | P2 | buyer-je8 |  |
| \[ ] | buyer-w5c | 3E: Builder Guides for Vertical Customization | P2 | — |  |
| \[!] | buyer-1o3 | 3F: Deployment & Operations Guide | P3 | buyer-j95 |  |
| \[ ] | buyer-j95 | 3G: Infrastructure-as-Code Deployment (CloudFormation/Terraform) | P3 | — |  |

## Phase 4 — DealJockey — Deal Portfolio Management

### Phase 4A — MVP DealJockey

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[x] | buyer-te6b.1.4 | Add manual deal entry | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.5 | Build portfolio inspection tools | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.2 | Create DealJockey L2 agent in buyer hierarchy | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.12 | Deal library CRUD operations | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.7 | Define DealJockey event types (Phase 1) | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.11 | Extend DealStore schema | P1 | — | 2026-03-18 |
| \[x] | buyer-te6b.1.3 | Implement CSV deal import parser | P1 | — | 2026-03-18 |

### Phase 4B — Templates & Seller Integration

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[x] | buyer-te6b.1.6 | Organize internal deal-booking modules (consolidate per ar-fad) | P1 | — | 2026-03-19 |
| \[x] | buyer-te6b.1.1 | Write DealJockey seller API contract (supply-chain, from-template, bulk, performance) | P1 | — | 2026-03-19 |
| \[x] | buyer-te6b.1.13 | [buyer-dj5] Deal template and supply path template CRUD | P1 | — | 2026-03-19 |
| \[ ] | buyer-te6b.2.7 | [buyer-dj6] AnalyzeSupplyPathTool | P2 | — |  |
| \[ ] | buyer-te6b.2.8 | [buyer-dj7] InstantiateDealFromTemplateTool | P2 | — |  |
| \[x] | buyer-te6b.1.8 | [seller-dj2] Add GET /api/v1/supply-chain endpoint | P1 | — | 2026-03-19 |
| \[x] | buyer-te6b.1.9 | [seller-dj3] Add POST /api/v1/deals/from-template endpoint | P1 | — | 2026-03-19 |
| \[x] | buyer-te6b.1.10 | [seller-dj4] Add GET /api/v1/deals/{id}/performance endpoint | P1 | — | 2026-03-19 |

### Phase 4C — Portfolio Intelligence

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[ ] | buyer-te6b.2.2 | Build cross-path price comparison tool | P2 | — |  |
| \[ ] | buyer-te6b.2.4 | Build deal deprecation analysis and execution | P2 | — |  |
| \[ ] | buyer-te6b.2.6 | Build human instructions adapter for manual deal migration | P2 | — |  |
| \[ ] | buyer-te6b.2.12 | Define DealJockey event types (Phase 2) | P2 | — |  |
| \[ ] | buyer-te6b.2.3 | Implement deal duplication for new advertisers | P2 | — |  |
| \[ ] | buyer-te6b.2.1 | Implement deal portfolio gap analysis | P2 | — |  |
| \[ ] | buyer-te6b.2.5 | Implement portfolio health reporting | P2 | — |  |
| \[ ] | buyer-te6b.2.11 | [buyer-dj12] Deal migration tool (MigrateDealsTool) | P2 | — |  |
| \[!] | buyer-te6b.2.9 | [buyer-dj8] BulkDealOperationTool | P2 | buyer-te6b.2.14 |  |
| \[ ] | buyer-te6b.2.10 | [buyer-dj9] GetDealPerformanceTool | P2 | — |  |
| \[ ] | buyer-te6b.2.13 | [seller-dj5] Enhanced supply-chain with sellers.json and schain | P2 | — |  |
| \[ ] | buyer-te6b.2.14 | [seller-dj6] Add POST /api/v1/deals/bulk endpoint | P2 | — |  |

### Phase 4D — Platform Integrations

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[ ] | buyer-te6b.3.4 | Amazon DSP API connector for deal import | P3 | — |  |
| \[!] | buyer-te6b.3.8 | Cross-platform deal deduplication | P3 | buyer-te6b.2.2 |  |
| \[ ] | buyer-te6b.3.2 | DV360 API connector for deal import | P3 | — |  |
| \[ ] | buyer-te6b.3.6 | Mediaocean Lumina export parser | P3 | — |  |
| \[ ] | buyer-te6b.3.5 | Mediaocean Prisma export parser | P3 | — |  |
| \[ ] | buyer-te6b.3.1 | TTD API connector for deal import | P3 | — |  |
| \[ ] | buyer-te6b.3.3 | Xandr API connector for deal import | P3 | — |  |
| \[ ] | buyer-te6b.3.7 | [buyer-dj11] Cross-platform deal activation tracker | P3 | — |  |

### Phase 4E — External Model Integration

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[!] | buyer-te6b.4.4 | Add external optimization hooks to DealJockey | P3 | buyer-te6b.2.5, buyer-te6b.2.11 |  |
| \[!] | buyer-te6b.4.5 | ML-tuned supply path scoring | P3 | buyer-te6b.2.7 |  |
| \[ ] | buyer-te6b.4.1 | [buyer-dj14] Event system (Phase 4: optimization events) | P3 | — |  |
| \[ ] | buyer-te6b.4.2 | [buyer-dj15] Receive IAB Deals API v1.0 push updates | P3 | — |  |
| \[ ] | buyer-te6b.4.3 | [buyer-dj16] Curator awareness in SPO | P3 | — |  |
| \[ ] | buyer-te6b.4.6 | [seller-dj7] Curator support (OpenDirect 3.0) | P3 | — |  |

## Other

| | ID | Task | Priority | Blockers | Done |
|---|---|---|---|---|---|
| \[x] | buyer-2fb | Bug: Seller API auth resolves credentials from query params instead of headers | P1 | — | 2026-03-19 |
| \[ ] | buyer-1g4 | Bug: crewai Flow.kickoff() incompatible with FastAPI async handlers (seller endpoints) | P2 | — |  |
| \[x] | buyer-mt9 | Bug: crewai listen() API change crashes seller flow imports | P1 | — | 2026-03-19 |
| \[x] | buyer-pnf | Bug: discover_inventory.py still has inline tier discount math | P3 | — | 2026-03-19 |
| \[x] | buyer-947 | Bug: from-template missing flight date validation | P2 | — | 2026-03-19 |
| \[x] | buyer-gp3 | Bug: from-template missing flight date validation | P2 | — | 2026-03-19 |
| \[x] | buyer-111 | Bug: from-template missing_max_cpm returns 422 instead of 400 | P3 | — | 2026-03-19 |
| \[x] | buyer-tvy | Bug: from-template missing_max_cpm returns 422 instead of 400 | P3 | — | 2026-03-19 |
| \[x] | buyer-4xi | Bug: from-template returns wrong OpenRTB at value for PD deals | P3 | — | 2026-03-19 |
| \[x] | buyer-op8 | Bug: from-template returns wrong OpenRTB at value for PD deals | P3 | — | 2026-03-19 |
| \[x] | buyer-d9p | Bug: unused generate_deal_id import in unified_client.py | P4 | — | 2026-03-19 |
| \[ ] | buyer-nz9 | Order Status & Audit API Integration | P2 | — |  |
| \[x] | buyer-c8e | Task: Run live smoke tests for DealJockey seller endpoints | P1 | — | 2026-03-19 |

---
*Last updated: 2026-03-19 19:41 UTC — auto-generated by beads*
