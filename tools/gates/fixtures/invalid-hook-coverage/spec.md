# STORY-906 spec — widget lifecycle

## AC-1

**Given** a stale widget. **When** refreshed. **Then** the lifecycle hook fires.

**Test hooks:** `WidgetLifecycleTests`, `WidgetRepositoryTests.save`

## AC-2

**Given** a valid widget. **When** accepted. **Then** state is written.

**Test hooks:** `widget_accepts_valid`
