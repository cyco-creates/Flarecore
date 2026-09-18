# Light-source and Studio UX review — 2026-09-07

## Decision

Reduce nine top-level source choices to six jobs. Retain distinct solving algorithms beneath those jobs; do not silently change older renders.

| Previous mode | Current location | Why |
| --- | --- | --- |
| manual | Place light | Direct placement is independent of detection |
| detect | Detect bright sources | Per-frame bright-region detection |
| detect_with_manual_offset | Detection's Offset from picker modifier | Same detection plus a translation |
| track | Track bright sources → Match between frames | Identity association with hold/fade |
| lock | Track bright sources → Solve entire clip | Global path optimization; not the same as frame association |
| track_dots | Track bright sources → Track a dot matte | Relative threshold for synthetic dot plates |
| follow | Follow camera motion | Carries a placed source without detecting it |
| point_track | Track a chosen feature | Explicit feature matching; one or two trackers |
| path | Draw / edit a path | Authored or baked trajectory |

Source tuning is collapsible. Mode changes retain current numeric settings; Recommended settings explicitly applies starting values. Hold/fade are removed from whole-clip solving because that branch does not consume them. Feature smoothing and detection search radius are exposed because those branches do consume them. Connected external lights replace inactive source controls with an explanation rather than suggesting that changing the selector could override the connection.

## Legacy triggers

The legacy control panel is removed. New animation uses Optical response. The backend remains compatible with old rules, and affected elements show a notice with an undoable removal action. Automatic conversion is unsafe: additive brightness and trigger-relative geometry do not have a general equivalent in the new response multipliers.

## Forge and notes

The prompt editor is organized into labeled selection, prompt refinement and optional styling. The workflow is laid out in generation order without changing links, generator settings or presets. All four notes are rewritten, including the master feature guide. Both positional and named note text are updated; leaving the named text stale could restore obsolete instructions in a frontend extension.

## Verification and limits

- Source/render, feature tracking, scene motion and detection tests: 97 passed.
- Full Python suite after source-coverage and note/layout additions: 628 passed, including 9 workflow tests.
- Browser workbench uses the actual source-row builder and Forge setup with mocked ComfyUI widget storage and the shipped prompt/style bank. It checks all nine saved modes, six primary choices, control visibility, preserved tuning and external-input messaging. Saved custom prompt restoration, base-prompt reset and the optional style panel were exercised.
- Workflow checks cover depth links, widget compatibility, duplicate IDs, note text consistency, group containment and non-overlapping Forge nodes.

These are synthetic/regression and isolated browser tests, not new tracking-quality measurements on the user's footage or a paid generator run. Existing rendering algorithms are unchanged. Save and refresh the frontend, then reopen the updated Studio workflow for its new layout and notes.
