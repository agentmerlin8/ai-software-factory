# STORY-NNN — quickstart flag rename (docs-only change)

```diff
--- a/docs/onboarding.md
+++ b/docs/onboarding.md
@@ -12,7 +12,7 @@
-    python3 onboard.py propose --manifest
+    python3 onboard.py propose --plan manifest
@@ -30,7 +30,7 @@
-    python3 onboard.py propose --manifest
+    python3 onboard.py propose --plan manifest

--- a/docs/preflight.md
+++ b/docs/preflight.md
@@ -4,7 +4,7 @@
-  the preflight gate runs propose --manifest against each target
+  the preflight gate runs propose --plan manifest against each target
```

## Scope

Flag rename only: every invocation site in the two documents is
updated in this change, and no code or configuration is touched.