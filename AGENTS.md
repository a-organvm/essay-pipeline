<!-- ORGANVM:AUTO:START -->
## Agent Context (auto-generated — do not edit)

This repo participates in the **ORGAN-V (Public Process)** swarm.

### Active Subscriptions
- Event: `promotion.completed` → Action: Generate essay topic about promoted repo
- Event: `ci.passed` → Action: Track system health for sprint narratives

### Production Responsibilities
- **Produce** `essay-topic-suggestions` for ORGAN-V
- **Produce** `sprint-narrative-draft` for ORGAN-V
- **Produce** `essays-index` for ORGAN-V

### External Dependencies
- **Consume** `essay-markdown` from `{'organ': 'ORGAN-V', 'repo': 'public-process'}`
- **Consume** `system-activity` from `{'organ': 'ALL', 'repo': '*'}`
- **Consume** `process-documentation` from `{'organ': 'ORGAN-III'}`
- **Consume** `research-discourse` from `{'organ': 'ORGAN-I'}`
- **Consume** `governance-documentation` from `{'organ': 'ORGAN-IV'}`

### Governance Constraints
- Adhere to unidirectional flow: I→II→III
- Never commit secrets or credentials

*Last synced: 2026-04-14T21:32:08Z*
<!-- ORGANVM:AUTO:END -->
