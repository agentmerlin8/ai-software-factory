# STORY-NNN — contract types refresh (plan excerpt)

## Change

- `LaneRecord` gains two fields: `retry_count: number`, `owner_handle:
  string` (schema v17, frozen decision log).
- `src/contracts/dto.ts` is updated in this story.

## Not in scope

- `src/contracts/types.ts` stays at v16 and is untouched in this story.

## Fixtures

- `fixtures/lane.builder.ts` imports `LaneRecord` from
  `src/contracts/types.ts`; fixtures are regenerated only when that
  file changes.

## Checks

1. `pnpm vitest run` — fixture suite
2. `pnpm build-storybook` — storybook bundle