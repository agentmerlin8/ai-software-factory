// src/features/lane/ConfirmDialog.tsx — review package excerpt.
//
// The parent mounts one dialog above the lane and toggles `open`; the
// avatar card behind it selects which target (delete | archive) the
// next confirmation covers.

import { useState } from "react";

type Target = "delete" | "archive";

export function ConfirmDialog({ target, open }: { target: Target; open: boolean }) {
  const [mode, setMode] = useState<Target>("delete");
  const [confirmed, setConfirmed] = useState(false);

  function handleConfirm() {
    setConfirmed(true);
    dispatch({ type: "action-confirmed", detail: mode });
  }

  return (
    <dialog open={open ? "open" : undefined} aria-label={`confirm-${mode}`}>
      <p>Confirm {mode}?</p>
      <button type="button" onClick={handleConfirm}>Yes, {mode}</button>
    </dialog>
  );
}

// ConfirmDialog.test.tsx (excerpt): every test renders a fresh instance
// through the test-utils glue; no test re-opens the same mounted dialog
// with a different target.