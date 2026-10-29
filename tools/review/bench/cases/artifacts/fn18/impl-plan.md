# STORY-NNN — results card EXTEND (implementation plan excerpt)

## Manifest extend

1. EXTEND `results-card` -> `src/features/results/manifest.toml`
2. EXTEND `summary-stage` -> `src/features/profile/manifest.toml`

## Route map (as currently wired)

| route            | rendered components                    |
|------------------|----------------------------------------|
| /dashboard       | DashboardList, MetricsStrip            |
| /profile         | ProfileHeader, Timeline                |
| /preview/results | ResultsCard (developer preview only)   |

## Test placement

results-card.test.tsx is filed under src/pages/dashboard/. The dashboard
page renders DashboardList and MetricsStrip only and owns no mutation for
the card's fields; the results fetch lives on the parent page.