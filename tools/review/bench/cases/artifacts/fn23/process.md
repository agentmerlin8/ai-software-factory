# STORY-NNN — review lane process slide (excerpt)

## Concurrent external review

To overlap the external review with the internal review, the dispatcher
performs the schedule lines below.

1. Open the merge request in draft.
2. Mention the external reviewer inside the draft MR; the mention
   doubles as the scheduling request.
3. Start the internal review immediately so the two tracks overlap.
4. Record the overlap as validated when the ready-push lands, which the
   dispatcher observes after a wall-clock wait of two hours.
5. If no ready-push is observed within the wait, schedule the external
   turn serially after the internal verdict.

A window overlap is accepted whenever the ready-push and the internal
verdict both land inside the same two-hour window.