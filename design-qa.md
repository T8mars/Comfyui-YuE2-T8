# Music workbench design QA

Validation date: 2026-09-14

## Scope and evidence

The v1.4.2 audit covered the running local WebUI, async project workflows, asset transfer, YuE2 training controls and responsive navigation. The current run used an 820 px browser viewport across all workspaces and checked the existing 1600 × 1050 desktop and 390 px mobile baselines. Static DOM inspection found one `h1`, 11 `h2`, 12 `h3`, 115 form controls, 113 explicit labels and no duplicate IDs. The service health and core JSON routes responded successfully.

The responsive browser run is direct visual evidence for the 820 px layout. The 1600 px and 390 px statements reuse the prior browser baseline plus the same CSS and DOM reviewed in this release; they were not recaptured on additional physical displays. This report does not claim formal WCAG certification or untested GPU and operating-system support.

## Ten-round review

| Round | Area | Finding and resolution | Result |
| --- | --- | --- | --- |
| 1 | First-use orientation | The product purpose, runtime state, current project and primary workspaces remain visible; local generation and API/local-LLM assistant behavior are described accurately. | Passed |
| 2 | Navigation | At 820 px the former sidebar compressed headings and controls. It now becomes a scrollable top navigation and centers the selected workspace. | Fixed and passed |
| 3 | Model settings | The large shared settings panel obscured the next workspace after navigation. Switching workspaces now collapses it automatically. | Fixed and passed |
| 4 | Project isolation | Slow uploads, submissions, result polling and errors could outlive a project switch. Every asynchronous operation now retains its originating project. | Fixed and passed |
| 5 | Asset library | Slow search results could replace newer filters, and score assets lacked a complete transfer path. Request revisions now reject stale responses; ABC opens as an editable plan. | Fixed and passed |
| 6 | Creation and score flow | Project-specific results and latest-task cards could appear in the wrong workspace. Rendering now filters by active project and clears stale signatures. | Fixed and passed |
| 7 | Assistant | Initial project selection and failed scope changes could race draft restore and polling. Initialization is awaited and failed switches resume the prior scope safely. | Fixed and passed |
| 8 | Training | Training, preview and auxiliary jobs shared one mutable ID. They now use independent channels and per-run mappings, preserving pause/resume state. | Fixed and passed |
| 9 | Checkpoint handoff | Model transfer could use a newer mutable run instead of the selected result. Buttons now bind the exact result model ID and remain disabled if none exists. | Fixed and passed |
| 10 | History and regression | History requests now reject stale responses. The responsive history, training and score-transfer flows were rechecked; JavaScript syntax, diff hygiene and the full automated suite passed. | Passed |

## Outcome

No open P0, P1 or P2 issue remains from this audit. The final low-priority finding, a pre-submit upload or ABC-validation error appearing after the user changed projects, was resolved by binding the error renderer to the original project.

Final result: passed with stated evidence limits.
