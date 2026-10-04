# Vendored third-party skill

`ffmpeg-skill` v2.4.2, from https://github.com/kajisho5/ffmpeg-skill at `a991599`.
Upstream licence: MIT (see LICENSE beside this file).

Only the parts the skill runs from are vendored: `SKILL.md`, `scripts/`,
`references/`, `templates/`. Upstream's `docs/` (12 MB), `assets/`, `tests/`,
`evals/` and `demos/` are left out -- they are for developing the skill, not for
using it, and would have put 34 MB in this repo to no end.

To update: clone upstream at the new tag and re-copy those four paths. Do not edit
them in place; a local change here is lost on the next update and is invisible to
anyone reading the upstream repo.
