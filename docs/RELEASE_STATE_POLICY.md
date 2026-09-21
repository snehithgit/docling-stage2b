# Release-state documentation policy

Effective from release `2026.09.18.40.3`.

Every distributed project ZIP must include current copies of:

- `PROJECT_TRACKER.md` — current release, architecture, measured state and next phase;
- `PROJECT_COMPLETED.md` — features already implemented and not to be unnecessarily rebuilt;
- `PROJECT_IMPLEMENTATION_TODO.md` — remaining work with completed checkboxes preserved;
- `PROJECT_ACQUIRED_STATE.md` — assets/data/runtime already acquired;
- `REQUIRED_FOR_NEXT_PHASE.md` — acceptance gate and inputs required for the next implementation phase;
- `NEW_CHAT_HANDOFF.md` — concise continuation prompt/context for a fresh chat;
- `STAGEWISE_WORKFLOW.md` — hard sequential pipeline and ownership of each UI/workflow stage.

Before packaging, the release process must update these documents to the code being packaged. A ZIP is not considered release-complete if the tracking documents describe an older architecture or version.
