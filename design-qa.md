# Music workbench design QA

Validation date: 2026-09-14

## Scope

Compared the planned project, asset-library and training-workbench screens with the running v1.4.0 WebUI. The implementation keeps the product's established light blue-gray, white, navy and berry palette while carrying over the planned information architecture, dense workstation layout and persistent project context.

## Visual review

| Screen | Desktop result | Responsive result | Interaction result |
| --- | --- | --- | --- |
| Song project | Passed at 1600 × 1050 | No horizontal overflow at 390 px | Project selection, fixed asset versions and audio preview passed |
| Asset library | Passed at 1600 × 1050 | Cards collapse to one column at 390 px | Import type appears before file selection; waveform, playback, project linking and trained-model handoff passed |
| YuE2 training | Passed at 1600 × 1050 | Inspector stacks below the training canvas | Resource state, stages, localized status, loss chart, pause/resume controls, short-preview request and selected-model transfer passed |

The browser run reported zero console errors and zero failed HTTP responses. All visible buttons have text or an accessible name. Keyboard focus remains visible. The 390 px viewport measured a 390 px document width with no horizontal overflow.

## Resolved findings

- P1: Dynamic “send to song creation” controls initially lacked a delegated tab action. The final control resolves the trained model, selects it in the creation form, forces the supported direct-generation mode and then changes page.
- P1: Training completion initially linked only to history. The final training page renders a short-preview action and an inline audio player when the preview completes.
- P1: Training data initially had only explanatory rights copy. The final form requires confirmation and records it in the immutable snapshot; the API rejects a missing confirmation.
- P2: Programmatic focus inspection did not represent keyboard focus. The final CSS provides a consistent focus-visible outline for buttons, links and form controls, and keyboard navigation was checked directly.
- P2: Model assets initially used the generic text-view action. The final card uses “用于创作” and transfers the exact model asset.

No open P0, P1 or P2 findings remain.

final result: passed
