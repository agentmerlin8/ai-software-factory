# STORY-NNN — test plan verification (excerpt)

## Compilation and type-contract drift

Type-contract drift between `src/contracts/types.ts` and the fixture
builder output is verified by compilation through the standard pipeline
entry:

1. `pnpm vitest run`
2. `pnpm build-storybook`

Both commands are required green in CI before merge. A green run of
both is accepted as proof that the contract types and the fixture
builder stay in lock-step.

## Runtime smoke

3. `pnpm vite build`

The runtime smoke exists to catch bundle-level regressions only.