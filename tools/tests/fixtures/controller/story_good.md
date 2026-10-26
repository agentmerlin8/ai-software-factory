# Story — fixture

Author: approver-one
Structural signal: new module

UI: none

### AC-1 — refuses malformed input
- **Given** a malformed record
- **When** it is submitted
- **Then** the controller refuses and nothing is written

### AC-2 — records valid input
- **Given** a valid record
- **When** it is submitted
- **Then** exactly one event is appended
