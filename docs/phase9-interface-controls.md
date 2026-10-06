# Phase 9 — V5.0.8

Library summary filters now operate inside the library controller. Search availability is labelled separately from evidence validation; pipeline guidance includes embedding and search. Poll failures mark displayed library data as potentially stale, and successful recovery clears the error.

Review dispatch status comes from the saved worker registry, independently of unsaved checkboxes: stopped, blocked by primary work, actively reviewing, or enabled and waiting. API failure clears the ready indicator. Existing controls and unsaved assignment preservation remain in place.

Shared guidance explains that AI suggestions do not apply human corrections, and detected anomalies queue automatically while re-review preserves human decisions. No ledger format, inference scheduling, runtime lifecycle, or credential storage changes are included.

Validation: frontend interaction regressions, JavaScript syntax, release version synchronization, and Linux CI. Native browser visual inspection was unavailable because the browser blocks the LAN URL; layout remains unchanged.
