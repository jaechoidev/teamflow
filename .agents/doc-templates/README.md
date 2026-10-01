# Project notes

Templates for notes the team keeps current while it builds. Notes belong to
the Notetaker: teamflow creates `docs/notes/` when a Notetaker joins the team,
at start or when one is added. The templates ship with the installed
teamflow and are never copied into the project. Plain Markdown works in any
editor, and Obsidian reads the frontmatter as properties.

## Templates

| Template | Records |
| --- | --- |
| `code-map.md` | How an area of the current implementation fits together |
| `decision.md` | A choice, the options, and the rationale (ADR style) |
| `experiment.md` | A question answered by measurement |
| `source.md` | An external source and the claims used from it |
| `concept.md` | An idea, plus the user's own words and memory questions |

The first four are operational records, updated with each implementation
slice. Concept notes are the learning part.

## Creating a note

Notes live in the main checkout, where the Notetaker works, one folder per
type and one file per note:

```
docs/notes/
  code-maps/  concepts/  decisions/  experiments/  sources/
    2026-09-30-2041-film-accumulation-and-rng.md
```

A file name starts with the note's creation time, `YYYY-MM-DD-HHMM`, so a
folder lists notes in the order they were written. The rest is the title in
lowercase, with dashes between words. The name never changes after that,
even when the note is updated, because links to the note use it.

Create every note with `teamflow note new`. It picks the folder, names the
file, fills the template's title and date, refuses to overwrite a note or to
repeat a title of the same type, and prints the new note's path:

```
teamflow note new concept "Task mailbox"
```

In a worker window, `"$TEAMFLOW_HOME/scripts/teamflow"` is the team's own
launcher, in case `teamflow` is not on `PATH` there.

Link a note by its file name without `.md`, for example
`[[2026-09-30-2041-film-accumulation-and-rng]]`. Obsidian finds it in any
folder. `[[2026-09-30-2041-film-accumulation-and-rng|Film accumulation]]`
shows a shorter label.

Obsidian is optional. To browse, open the main checkout's `docs/` as a
vault so notes sit beside Superpowers specs and plans. Create notes with
`teamflow note new` rather than Obsidian's Templates plugin, which names
files differently. Keep `docs/.obsidian/` out of commits unless the user
wants shared settings. Init does not create a vault or `docs/`. Teamflow
creates `docs/notes/` with the Notetaker but does not configure Obsidian.

## Superpowers specs and plans

Superpowers writes proposed designs to `docs/superpowers/specs/` and plans
to `docs/superpowers/plans/`. Those describe intended work. Notes record
what holds afterwards: a code map describes the code as built, a decision
keeps the rationale, and an experiment keeps measured results. Link a spec
or plan. Never copy its text.

A spec or plan written in a developer worktree, or kept in a task record,
reaches the main checkout only through approved integration or promotion.
Keep promoted ones in `docs/superpowers/specs/` and
`docs/superpowers/plans/` so notes can link them.

## Frontmatter

Every template starts with these properties, in this order:

| Property | Value |
| --- | --- |
| `type` | `source`, `concept`, `code-map`, `decision`, or `experiment` |
| `status` | `draft`, `revised`, `reviewed`, or `superseded` |
| `created` | creation date, `YYYY-MM-DD` |
| `reviewed_by` | last reviewer: a role id or `user` |
| `reviewed_on` | last review date |
| `reviewed_commit` | commit the code claims were checked at, quoted: `"abc1234"` |
| `superseded_by` | the replacement note: `"[[2026-10-02-0915-new-note]]"` |
| `code_paths` | repo paths the claims depend on, e.g. `[scripts/teamflow]` |

`source.md` adds `url`. Leave a value empty rather than guess.

## Status

- `draft`: new, or never reviewed.
- `reviewed`: someone other than the last editor checked the claims against
  their evidence. The review sets `reviewed_by`, `reviewed_on`, and, when
  `code_paths` is not empty, `reviewed_commit`. That commit must be on the
  default branch. Role-branch hashes do not survive integration.
- `revised`: a reviewed note had a substantive edit and awaits review. The
  old review fields stay until the next review.
- `superseded`: replaced by the note in `superseded_by`. Leave it as is.

A substantive edit changes a fact (a claim, number, path, decision, or
result) in any section, own words included. Typos, formatting, links, and
question wording are not substantive. When the user makes a substantive
edit to a reviewed note, the Notetaker sets `revised` on seeing it. Status
tells readers how far to trust a note. It never blocks work.

## Per slice

1. The Delegator reads the completed task, reviews and integrates code, then
   runs `task.sh release <id> [integrated-commit]`. The event-driven queue
   sends one released task to the Notetaker. It does not poll while a note is
   being written.
2. The Notetaker reads the result and investigates its underlying code,
   tests, commits, discussion, specs, plans, and original sources as needed.
   Find affected notes, e.g. `rg -l 'src/auth' docs/notes`.
3. Update or create notes from verified evidence. Create a note with
   `teamflow note new <type> "<title>"`. New notes start as `draft`.
   A substantive edit turns `reviewed` into `revised`. If nothing durable
   changed, record that in the completion summary.
4. After saving files, run `task.sh noted <id> '<summary and note paths>'`.
   This removes that completed task and sends the next released task. The
   Delegator reads the summary under `.git/teamflow/note-queue/completed/`.
5. Notes are tracked docs: commit changes only with the user's approval,
   ideally together with the slice they describe.

## Milestones (optional)

There is no milestone gate by default. When useful, the Delegator lists
notes worth a review and offers one:

```
rg -l '^status: (draft|revised)' docs/notes
git log --oneline <reviewed_commit>..HEAD -- <code_paths>
```

The first lists unreviewed notes. The second shows whether code under a
reviewed note changed since its review.

## Who does what

- Notetaker: investigates evidence, keeps notes current, and sets honest
  statuses. Only the Notetaker does this. Without one there are no notes,
  and released tasks wait until a Notetaker joins.
- Delegator: releases verified work and reads the note completion summary.
- Developers and Researcher: supply evidence in task results, and edit
  notes only when assigned.
- Reviews: the Reviewer for code claims, the Researcher for sources, or the
  user. Whoever reviews sets the review fields.
- User: owns their understanding and writes the own-words explanations and
  memory answers. Agents may propose memory questions.
