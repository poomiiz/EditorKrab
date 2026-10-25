# Launch kit

Release and announcement materials. No Release has been published yet; check [Releases](https://github.com/poomiiz/EditorKrab/releases) before announcing. No social or community posts have been sent as part of this preparation.

Use the [promotion drafts](promotion.md) for copy and a 30-second demo plan. Check [Releases](https://github.com/poomiiz/EditorKrab/releases) before telling users to update.

## Project description

EditorKrab is an open source Windows desktop app that turns talking-head footage into short vertical clips with subtitles, on your own computer.

Announce **Windows 10/11 only**; Mac is untested on real hardware. Every clip needs human review. Editing-time savings are unmeasured. State the [known limits](../README.md#known-limits) in every announcement.

## Existing assets

| Asset | Use |
|---|---|
| [logo.svg](brand/logo.svg), [logo.png](brand/logo.png) | Wordmark on a light background |
| [logo-dark.svg](brand/logo-dark.svg), [logo-dark.png](brand/logo-dark.png) | Wordmark on a dark background |
| [icon.svg](brand/icon.svg), [512 px](brand/icon-512.png), [1024 px](brand/icon-1024.png) | App icon |
| [GitHub banner](brand/github-banner.png) | Repository social preview |
| [Promo](brand/promo.mp4), [GIF preview](brand/promo.gif) | 33-second workflow illustration, silent with English captions |
| [Voice-over script](brand/promo-voiceover.md) | Matching lines and timings for recording |

The promo illustrates draft preparation, review and export. It still labels Mac setup experimental, which does not match the Windows-only launch, so update that caption before using it. It makes no measured speed claim. Use the separate [demo plan](promotion.md#30-second-demo-plan) to record the real workflow with consented footage.

Editable SVG sources are included. The existing palette uses navy, coral and off-white. The Windows icon is [editorkrab-crab.ico](../rust/data/editorkrab-crab.ico).

The plugin ID `clip-kit` and existing `clipkit` file-format names remain unchanged for compatibility.

## Release and announcement checks

| Check | Required evidence | Current status |
|---|---|---|
| Release delivery | Published tag/Release at the intended commit; install verified on a clean Windows machine | Not done: the repository has no tags or Releases yet (checked 2026-10-19) |
| Software checks | CI at the release commit and required original security hotfix ancestry | Check at the release commit; all commits in the [release inventory](release-inventory.json) must be ancestors of it |
| Windows editing | A real source, exported pilot and the quality checklist | Record the actual result; software tests alone are insufficient |
| Mac editing | — | Out of scope: the desktop app is Windows only |
| Timing claim | Completed human benchmark with CSV and trial-scoped log | UNKNOWN; no real percentage available |
| Public assets | Consent for demo footage, no private data, third-party attribution reviewed | Existing brand art and revised illustration reviewed; choose consented footage for the recorded demo |

When publishing an early access release, state the unresolved checks plainly. Before calling a release stable, try the editing, installation and team service with real users and devices.

## Prepare the announcement

1. Use consented footage or a purpose-made sample. Keep client names, private paths and account details out of the capture.
2. Record the steps in the [demo shot list](promotion.md#30-second-demo-plan). Show the draft review before the final MP4.
3. Use the English or Thai copy for the chosen channel after verifying the release availability.
4. Link to the repository, installation guide and known limitations.
5. Upload the banner as GitHub's social preview and post the announcement only when the owner is ready to publish.

Copy, the workflow illustration, shot list and asset references are prepared here. A recorded demo, social posts and paid media generation are separate actions.
