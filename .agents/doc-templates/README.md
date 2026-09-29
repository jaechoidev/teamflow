# Project notes

Opt-in templates for notes the team keeps current while it builds. Nothing
changes in a project until the user adopts them. A project has adopted
them once its notes folder exists or the user says so. Plain Markdown works
in any editor, and Obsidian reads the frontmatter as properties. `ai-team
init` copies this folder and, as with roles, stages a changed shipped file
as `<file>.new` instead of overwriting yours.

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

Notes live in the main checkout, where the Delegator works. The default
folder is `docs/notes/`. A project that uses another records it in
AGENTS.md outside the ai-team markers. Use one file per note, named in
kebab-case. `set -C` makes this refuse to overwrite an existing note:

```
mkdir -p docs/notes
( set -C; sed -e 's|{{title}}|Task mailbox|g' -e "s|{{date}}|$(date +%F)|g" \
  .agents/doc-templates/concept.md > docs/notes/task-mailbox.md )
```

In a title, put a backslash before any `&`, `|`, or `\`. Copying a
template by hand and filling in `{{title}}` and `{{date}}` works too.

Obsidian is optional. To browse, open the main checkout's `docs/` as a
vault so notes sit beside Superpowers specs and plans. Its core Templates
plugin fills `{{title}}` (with the file name) and `{{date}}` too, but it
only reads templates inside the vault, so copy these files to
`docs/templates/` to use it. Keep `docs/.obsidian/` out of commits unless
the user wants shared settings. Init never creates a vault or `docs/`.

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
| `superseded_by` | the replacement note: `"[[new-note]]"` |
| `code_paths` | repo paths the claims depend on, e.g. `[scripts/ai-team]` |

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
edit to a reviewed note, the Delegator sets `revised` on seeing it. Status
tells readers how far to trust a note. It never blocks work.

## Per slice

1. The worker's task result carries the evidence: changed paths, commit,
   tests run and their results, and sources.
2. The Delegator finds affected notes, e.g. `rg -l 'src/auth' docs/notes`.
3. The Delegator updates or creates the notes, or dispatches the edit. New
   notes start as `draft`. A substantive edit turns `reviewed` into
   `revised`.
4. Notes are tracked docs: commit changes only with the user's approval,
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

- Delegator: keeps notes current and statuses honest.
- Developers and Researcher: supply evidence in task results, and edit
  notes only when assigned.
- Reviews: the Reviewer for code claims, the Researcher for sources, or the
  user. Whoever reviews sets the review fields.
- User: owns their understanding and writes the own-words explanations and
  memory answers. Agents may propose memory questions.
