# Admin console: design notes

The admin console is a trust console. People open it because they need to check something: who ran
what against production, whether an access change landed, why a connection is red. It sits next to
an IAM console or a database admin panel, not next to a consumer app. So the design's job is to make
dense, consequential information fast to scan, easy to trust and hard to misread.

These notes explain the choices, so the next person to touch the UI can keep them consistent.

## Colour

Seven named values, each with one job. Colour carries meaning here, so it is never decoration.

| Token | Hex | Used for |
|---|---|---|
| `ground` | `#F1F3F0` | The page behind the tables. A cool, faintly green-grey paper. |
| `paper` | `#FFFFFF` | Table and form surfaces, so text sits on maximum contrast. |
| `ink` | `#15212B` | Text, and the dark navigation rail. Blue-black rather than pure black. |
| `rule` | `#C8D0D2` | Hairlines between rows and around inputs. |
| `live` | `#0A7559` | **Only** "this is currently active": a grant that is in effect, a connection that just passed its check. Never used for plain success. |
| `fault` | `#B3261E` | **Only** errors and destructive actions: a failed call, a failed connection, the Revoke and Delete buttons. |
| `pending` | `#9A5B00` | **Only** "changed but not saved yet". Amber means the screen and the database disagree. |

Successful audit rows are not coloured at all. Most calls succeed, and a wall of green would hide the
few that don't. Failures get a red left edge and stand out because everything around them is quiet.

Contrast (against `paper`): ink 15.7:1, live 5.6:1, fault 6.4:1, pending 5.0:1, all above the 4.5:1
floor for text. On the dark rail, text is `#E8ECEE` (13:1) and secondary text `#9FB0B9` (6.4:1).

## Type

**IBM Plex Sans** for the interface and **IBM Plex Mono** only where the content really is code:
SQL, table and column names, IDs, timestamps in the detail view.

Plex Sans was designed for dense technical interfaces. It has tabular figures (so numbers in the
duration and row-count columns line up), clear distinctions between `I`, `l` and `1`, and a low-key
personality that doesn't compete with the data. Both are bundled with the app; an internal tool
shouldn't make requests to a font CDN.

Sizes are small and deliberate: 13px body in tables, 12px for secondary text, 20px for page titles.
Numbers are right-aligned with `font-variant-numeric: tabular-nums`.

## Layout

Everything is left-aligned except numbers, which are right-aligned.

**Shell.** A dark ink rail on the left (fixed, 208px) and a light working area. The rail holds the
product name, the six screens, and at the bottom who is signed in: name, role, and a sign-out button.
Identity lives there permanently because this is a multi-user tool. Below 860px the rail collapses to a
top bar with a menu.

```
+--------+----------------------------------------------------------+
| SQL    |  Audit log                                   [Tool calls|Admin changes]
| data   |  --------------------------------------------------------|
| layer  |  filters: [any user v] [any tool v] [any db v] [any outcome v] [date range] [search ]
|        |  --------------------------------------------------------|
| Audit  |  time         caller       tool         db        rows  ms |
| Perms  |  ▌ 14:02:11   ana          run_query    shop-pg     42  18 ▂|   <- red edge = failed
| Conns  |    14:01:58   ben          list_tables  shop-pg      -   4 ▁|
| Schema |    ...                                                     |
| Health |  --------------------------------------------------------|
| Reports|  1-50 of 1,204                          < prev  next >     |
|        |                                                            |
| Ada A. |                                                            |
| admin  |                                                            |
| Sign out                                                            |
+--------+----------------------------------------------------------+
```

**Audit log (the one place the design speaks up).** It is the most-used screen, so it gets the
identity. A single line of filters reads left to right like a query. Rows have a 3px left edge that is
red for failures and otherwise empty. Duration has a hairline bar under the number so slow calls show up
in a scan, not only by reading digits. Rows expand in place to show the full SQL, arguments and error.
Keyboard: `↑`/`↓` move between rows, `Enter` expands, `/` jumps to search, `Esc` closes.

**Permissions grid.** Rows are users and roles; columns are tables (plus one leading column, "can use
this database"). One database at a time, chosen from a selector, with a search box on the column axis and
per-row and per-column "all/none". A "flip" button swaps the axes, because with many tables the grid
is easier to read the other way round. Changes are *staged*: a toggled cell turns amber and a bar at the
bottom says "3 pending changes" with Save and Discard. Saving anything that revokes access first shows
exactly what will be revoked, from whom, and what that means for their next query.

```
  Database [ shop-pg v ]   tables: [ filter... ]                 [flip axes]
  ------------------------------------------------------------------------
                    can use   categories customers orders  payments ...
  role  analyst       [x]        [x]        [x]      [x]      [ ]
  role  viewer        [ ]        [ ]        [ ]      [ ]      [ ]
  user  Ana A.        [x]        [x]        [x]      [!]<- pending (amber)
  ------------------------------------------------------------------------
  1 pending change: grant payments to Ana A.                [Discard] [Save]
```

**Connections.** A table (name, engine, status, last checked, who may use it) and a side panel for
adding and editing. Credentials are write-only: the field says "Stored. Not shown." and only accepts a
replacement. The panel has a Test button that reports failures in plain words.

**Schema editor.** Two panes: tables on the left, the selected table on the right. Descriptions are
edited in place (click, type, Enter to save), with an explicit saved or unsaved marker. It is a
content-editing screen, so it has as little chrome as possible.

**Health.** A row of large, precisely labelled readings separated by hairlines (not cards), and two thin
time-series lines. No metrics wall.

**Saved reports.** A plain table and a side panel. Deliberately unfinished.

## Principles

1. **Consequential changes are staged and named.** Toggling access, saving a connection or editing a
   description never happens silently. The screen always says whether it matches the database
   (amber) and, before anything is revoked or deleted, says specifically what will change.
2. **Show the evidence, not a summary.** Raw SQL in monospace, exact timestamps, exact counts. An
   auditor should never have to trust a label the console made up.
3. **Colour is a claim.** Green means *currently in effect*, red means *wrong or destructive*, amber
   means *unsaved*. Nothing else uses them, so they can be believed.
4. **The console never shows a secret and says so.** Wherever a credential could appear it says
   "Stored. Not shown." and offers only replacement.
5. **Quiet except where it counts.** The audit log carries the personality; every other screen is
   plainer, so the eye learns where the important information lives.

## Reviewing the first plan

The first draft of this design failed the "would a generic prompt produce this?" test. It had a stats
row of four rounded cards on the health page, a green tick and red cross on every audit row, and a
neon-on-navy palette because control rooms are dark. Three changes came out of that review:

- The health page's cards became a hairline-separated row of readings and two lines. Cards made a
  handful of numbers look like a dashboard product; readings look like an instrument.
- Success is no longer coloured. Green was doing two jobs ("succeeded" and "granted"), which makes
  both meaningless. Now green means only *live*, and the audit log's personality comes from the failure
  edge and the duration bars instead.
- Dark navy for the whole app was dropped. Long sessions reading tables are easier on a light working
  area, and confining the dark to the rail gives the shell an identity without hurting legibility.

Motion is minimal and functional: a toggle's state change, a save confirmation, and nothing else.
`prefers-reduced-motion` turns the rest off.

## Accessibility

Every interactive element has a visible focus ring (2px ink with a white gap, inverted on the dark
rail). Every screen is fully usable from the keyboard. State is never conveyed by colour alone: failed
rows also say "failed", pending cells also carry a marker, and toggles are real checkboxes.
