# VLMD Subtask 1 — Cognitive Mechanism Classification

Built 2026-09-19 against Potato 2.9.3, Python 3.9.6, from `args_0911.json`
(sha256 `a63b826824a7…`).

---

## The unit

One **argument**: a claim plus only its immediate children. 1,621 of them over
614 memes. Settled in the brief, not revisited.

The meme image is **not shown**. The children typed `element` are written
descriptions of what is in the image, and they are the annotator's only account
of it. That is a decision with consequences — see *Assumptions*, first entry.

## The questions, and their final names

Names are storage keys. Renaming one after annotation starts splits the data
across the admin view, the agreement report and the export, silently, so these
are frozen — and have been broken once, on purpose, when the mechanisms were
flattened. `scripts/migrate_flatten_mechanisms.py` is what that cost, and it is
the shape of what any further rename would cost.

| Scheme | Type | Required | Shown |
|---|---|---|---|
| `section_sensible_test` | pure_display | no | always |
| `sensible_test_failed` | radio Yes/No | **yes** | always |
| `section_mechanism` | pure_display | no | gated |
| `mechanisms` | multiselect, 6 labels | **yes** | gated |
| `justification_affective_hijacking` | text | **yes** | when that mechanism is ticked |
| `justification_episodic_overgeneralization` | text | **yes** | ″ |
| `justification_spurious_causal_correlation` | text | **yes** | ″ |
| `justification_satirical_trivialization` | text | **yes** | ″ |
| `justification_esoteric_code_activation` | text | **yes** | ″ |
| `justification_spurious_legitimacy_signaling` | text | **yes** | ″ |
| `notes` | text, multiline | no | always |
| `flag_for_discussion` | multiselect, 1 label | no | always |

*gated* = `display_logic` on `sensible_test_failed` equalling **Yes**, so the
block appears the moment the Sensible Test comes back failed.

**The mechanisms are flat.** One multiselect, every mechanism at work in the
argument ticked in it, one justification box per tick, and nothing ranking
them. There is no primary and no secondary — the task used to ask for a
`primary_mechanism` radio plus a `secondary_mechanisms` multiselect, and that
distinction is gone from the config, the export, the agreement report, the
console and the guidelines. `mechanisms` is `required: true`, which is what
makes "the Sensible Test failed, so at least one mechanism is present" a thing
the page enforces rather than a sentence in the guidelines.

Three things followed from flattening, and they are the reason it is cheap:

* **A whole class of error disappeared.** The same mechanism could be recorded
  as both the primary and a secondary; a list of checkboxes cannot hold a label
  twice, so `primary_repeated_as_secondary` is gone from the export's
  validator rather than reimplemented.
* **The mechanism question is now scored as a set.** See the table below.
* **Answers stored under the old scheme names have to be moved.** Potato keys
  by scheme name, so they would be on disk and invisible.
  `scripts/migrate_flatten_mechanisms.py` folds them in, keeps a backup, and
  reports the two cases it cannot decide mechanically. What it cannot carry
  over is which mechanism was the primary one: the flat scheme has nowhere to
  put it. The backups are the only record of it afterwards.

**Notes and the flag carry no heading.** A `section_record` pure_display
reading *Step 3 — Anything else* sat over them and rendered in the wrong place:
with no `layout` block the forms flow in a grid, a heading is a form like any
other, and it landed in a column beside the last justification box rather than
above Notes. Steps 1 and 2 keep theirs because Step 2's heading carries the
same gate as the questions under it and so appears and disappears with them.
Notes and the flag are always on screen and are labelled in their own right.

**The three checks are read, not answered.** They live as prose inside the
`section_sensible_test` heading — word for word from `guidelines.md`, which
`scripts/serve.py` verifies at boot — and the annotator collapses them into the
one `sensible_test_failed` verdict. Nothing is recorded per check, and the
export's `sensible_test` column is that verdict read the other way round: Yes
is a `Fail`, No is a `Pass`, unanswered is a draft.

**Names likely to need changing: none, with one caveat.** The six
`justification_*` names are derived from the mechanism labels. If a mechanism
is ever **renamed**, its scheme name must not be — rename the label, leave the
key. `scripts/export_annotations.py` maps label → scheme name in one dictionary
(`JUSTIFICATION`); change it there and nowhere else.

There is no overall Pass/Fail question. It is derived in the export: three No is
a Pass, any Yes is a Fail. The spreadsheet's single Pass/Fail column is the
thing the guidelines contradicted themselves about, so the task does not collect
it and no annotator can record it the wrong way round.

## Response formats and the agreement metric

| Scheme | Kind | What the report does with it |
|---|---|---|
| `sensible_test_failed` | nominal yes/no | Cohen's κ, and the 2×2 behind it |
| `mechanisms` | a set per annotator | five ways two sets can differ; positive specific agreement per mechanism |
| every justification, `notes`, `flag_for_discussion` | free text / optional | **nothing** — not scored |

The Sensible Test is a genuinely unordered yes/no, so a nominal coefficient is
right there. Nothing in this task is an ordered rating, so no scheme is at risk
of the "1–5 stored as a radio" mis-scoring.

**The mechanism question has no single coefficient, on purpose.** Potato would
infer `multilabel` from the scheme type and compute α-MASI and mean Jaccard,
and an earlier version of this report printed both. They were dropped with
Krippendorff's α, Fleiss' κ and the Landis & Koch bands, because a set-valued
answer collapsed to one number between 0 and 1 cannot say *what* two people
disagree about, and that is the only thing anybody opened the page to find out.

What replaced them is a partition. Every shared argument falls in exactly one
of five categories — identical sets, overlap with one having extra, overlap
with both having extras, one said None, no shared mechanism — and those five
counts are the report. They are mutually exclusive, they add up to the number
of shared arguments, and each one implies a different fix: extras are a
threshold problem, *no shared mechanism* is a definitions problem, and *one
said None* is really a Sensible Test problem wearing a mechanism's clothes.

Per mechanism the number is **positive specific agreement**, `2 × both ÷ (A +
B)`, and not a κ. The question is "when one of them reaches for this label,
does the other agree", and the arguments where neither used it — the
overwhelming majority — say nothing about that. A κ counts them as agreement
and reads high for a label nobody uses.

Cohen's κ survives on the Sensible Test alone, where there is one answer each
out of two, which is the shape κ was built for.

## Assignment

```yaml
num_annotators_per_item: {default: 10}
assignment_strategy: fixed_order
# max_annotations_per_user is deliberately absent
```

**The memes come in the source file's order; each meme is read bottom up.**
`build_instances.py` keeps the memes in the order they first appear in
`args_0911.json` — their arguments are contiguous there — and turns each meme's
arguments leaves first, so an argument built only out of visual elements and
background is read before the argument that then uses its claim as a child, and
the meme's root claim C0 is read last. The study opens on
`149_image` claim #1 (*Trump attempted to secure exclusive, for-profit control
over a coronavirus vaccine*), then its parent C0; then `148_image` claim #4,
claim #1, C0. Nothing is shuffled — the 614 memes are in file order.

That ordering is what `data/instances.json` is written in and what `fixed_order`
serves top to bottom. `item_number` (1-based) is the position in *that* order:
it is the number printed at the top of the argument, the number the navbar's
"Go to" box takes, and `Instance #N` in the navbar. Who annotates which stretch
is still settled between people, out loud, by number — "you start at 500" — and
the number is the same for everybody.

**Reading order is not just the reverse of the file.** Most memes run root-first
in the source, so bottom-up is an exact reversal of their block, but on the
2026-09-11 source 196 (claim, child) edges run the other way, and a plain
reversal would read those parents before their children. The build instead gives
each argument a *height* — the longest chain of claim-children below it that are
themselves arguments of the same meme — and sorts each meme by ascending height,
breaking ties by reverse source order. Height 0 is a leaf argument; C0 is
highest. The manifest records `n_order_violations`, which is 0 on this source:
no argument is served before an argument for one of its claim-children.

**The line each argument came from travels with it.** `source_index` (0-based)
is its position in `args_0911.json`, and the manifest's
`item_number_to_source_index` is the whole map, so the served order can be
checked against the source file without running the study.

**Everybody gets all 1,621.** `max_annotations_per_user` is not set. Unset means
the per-user quota is the item count, and under `fixed_order` Potato hands out
the whole remaining quota in one go, so each annotator's ordering is the whole
corpus in reading order from their first page. Three things follow, and all
three are the point:

- the navbar's "Go to" box reaches any argument, because the box indexes that
  annotator's ordering and their ordering is everything;
- `Instance #N` in the navbar and `Argument N of 1621` on the item are the same
  argument, and the manifest says which line of `args_0911.json` that is;
- two people can be on the same argument, which is how you collect agreement
  now: give them overlapping stretches.

**`num_annotators_per_item.default: 10` is what makes that possible.** An item
counts an annotator who merely *holds* it, not only one who has answered it.
With the old `default: 1`, the first annotator to arrive was handed all 1,621
and saturated every one of them, and the second annotator was served nothing
but the overlap set. 10 is a roster size nobody will reach — a ceiling, not a
target. Measured with three annotators registering in turn: each was handed
1,621 items and each landed on argument 1.

| `max_annotations_per_user` | `default` | Annotator 1 | Annotator 2 | Annotator 3 |
|---|---|---|---|---|
| unset | `10` | all 1621 | all 1621 | all 1621 |
| unset | `1` | all 1621 | 161 (overlap only) | 161 |
| `-1` | `1` | 1 at a time | 1 at a time | 1 at a time |
| `250` | `1` | 250 | 250 | 250 |

The second row is the one that looks like it works and does not: it does not
error and it does not warn.

*The tradeoff, stated plainly:* nothing stops two annotators doing the same
stretch by accident, and nothing guarantees the corpus is covered. Both are
visible — the console's **Assign** tab counts what is assigned, unassigned and
doubly assigned, and its **Who is where** table has each person's finished
ranges — but they are watched rather than enforced. That is the deal the numbering bought.

### The assignment overlay

Who has which stretch is *written down* rather than only said out loud, in
`annotation_output/assignments.json` (`scripts/assignments.py`):

```json
{"version": 1,
 "assignments": {"ey1220": [{"from": 1, "to": 320, "label": "batch A"}]}}
```

**It is an overlay, and that is the whole design.** Nothing in it reaches
Potato's assignment machinery: `assignment_strategy` is still `fixed_order`,
`max_annotations_per_user` is still absent, every annotator is still handed all
1,621 arguments in the one order. Each of those knobs hands out a contiguous
block whose first item becomes *that annotator's* argument 1, and the global
numbering is the thing this study is built on. So the file changes what an
annotator is **shown**, never what they can **reach**:

| | |
|---|---|
| the band | a strip under the navbar: their stretch, how much of it is finished, how much is left, how many drafts, and one button to the next argument in it they have not finished. `static/study.js` draws it from `/vlmd/batch` |
| the arrows | stop at the ends of the stretch instead of the ends of the corpus — but only while the annotator is *inside* it. Outside, they are the plain neighbours again, so an out-of-batch `Go to` cannot strand anybody |
| where they land | first arrival of a browser session goes to the first unfinished argument of the stretch rather than to argument 1, and only from outside it: somebody who stopped at 180 yesterday comes back to 180 |

`Go to` still reaches all 1,621, an argument outside somebody's stretch still
saves, and **an annotator with no entry in the file gets the study exactly as it
ran before any of this existed** — whole corpus, no band. So it ships dark, it
is turned on one person at a time, and a missing or corrupt file degrades to the
old study rather than breaking the new one.

*Soft, not hard, deliberately.* An out-of-batch argument warns in the band and
saves like any other. Two reasons: it matches everything else here, which is
watched rather than enforced, and a stretch is often extended verbally
mid-shift — an annotator told "keep going to 400" should not have to wait for
somebody to open the console. What the numbers are for is reading afterwards:
**Outside** in the console is finished work that fell out of somebody's stretch,
which is either an agreement set handed out on purpose or one handed out
verbally and never written down.

*Ranges, not a range.* The value is a list, appended to and never merged: a
second batch is a second entry rather than an edit, so the record of what was
handed out and when survives. An overlapping range on a second annotator is how
an agreement set is collected, and the console counts those arguments — two or
more annotators on an argument is the only overlap this study has, and the only
thing the coefficients have to be computed over.

**The gone machinery.** `overlap_sample`, the `overlap_stratum` field the build
used to stamp, and the 20-bucket stratification behind it are all removed: they
existed to raise a stratified 10% of the corpus to three annotators
automatically, and automatic assignment is exactly what was given up.
`instance_reclaim` is removed too — it took back items held by somebody who had
walked away, which only mattered while a hold could starve another annotator.

## Drafts

An annotator can leave an argument part-answered, move on, and come back to it.
`required: true` on a question means "needed before this argument counts as
done", not "needed before you may look at the next one" — Potato ties those two
together and `scripts/serve.py` separates them at boot.

How, exactly: the schemes are handed to Potato with `required: false`, which is
what both of Potato's gates read — the browser's, off the `validation="required"`
attribute it writes into the generated form, and the server's, in
`_check_required_or_block`. What `required` really means is put back underneath,
in the two places where it is about completeness rather than navigation:

| | |
|---|---|
| the navbar badge | `In Progress` for an argument with some answers but not all; `Labeled` only when it is finished |
| the progress counter | counts finished arguments; a draft is not one |

**Navigation is a scroll, not a filter.** Potato's two arrow buttons beside the
counter jump to the next and previous *unannotated* argument, and that is why a
finished argument used to be reachable only by typing its number into `Go to`:
the arrows stepped over it. `serve.py` points them at the neighbours instead —
every argument is one press from the one beside it, and the badge above says
which kind of argument you landed on. There is no wrap-around at either end, so
`static/study.js` retitles them (the template's tooltips read *Next
unannotated*), redraws them as plain arrows rather than skip chevrons, and
disables the one with nowhere to go — Potato answers a refused jump with *All
items have been annotated!*, which is a sentence about something else.

Nothing new is saved: every answer was already written as it was typed, through
`/updateinstance`. A draft is the same save without the gate.

**Two buttons, not one.** Potato has a single `Next`, and whether it will let
you past is invisible until you press it. `static/study.js` splits it:

| | |
|---|---|
| **Save draft** | leaves the argument unfinished and goes to the next one; never refuses |
| **Submit** | ends the argument, or refuses and names every question that has no answer, outlining each one *needs an answer* |

Submit **is** Potato's own button, relabelled — it keeps the save, the
navigation, the loading state and the disabled-while-saving handling, and
reimplementing those would be reimplementing the page. The validation hangs off
the global `navigateToNext` rather than off the button's `onclick`, so the
right-arrow key means Submit too, from one implementation.

The requirement list Submit checks is written into the page by `serve.py` from
the same schemes the server reads (`window.VLMD_REQUIRED`), so the button and
the badge cannot disagree about what finished means. It is needed because the
schemes reach Potato as optional: there is no `validation="required"` in the
generated form for Potato's own `validateRequiredFields()` to find. A question
behind a shut `display_logic` gate is skipped, read off the DOM rather than from
a second copy of the gate's rules — annotation.js is what opens and shuts them,
live.

The floor on a justification (`min_chars: 15`) is part of the completeness test
too, so an argument with a three-word justification reads `In Progress` while
the annotator can still do something about it — which is where that ought to be
caught, rather than in the export weeks later.

Nothing here relaxes what the export demands.
`scripts/export_annotations.py --strict` still refuses a study with drafts in
it: an unfinished argument has no Sensible Test verdict, and
`incomplete_sensible_test` is a constraint violation like any other.

## Effort

**1,621 annotator-items**, one per argument, if the corpus is annotated once.
Overlap is no longer automatic, so anything above 1,621 is a stretch somebody
was deliberately given twice.

`estimate_effort.py` from the potato-skill cannot read this config — it assumes
`num_annotators_per_item` is an integer and raises on the mapping form — so this
is the arithmetic by hand. A passing argument is three clicks; a failing one is
three clicks plus a written justification, which is where the time goes.

| Failure rate | Mean per item | Total | At $15/h |
|---|---|---|---|
| 25% | ~41s | 22 h | ~$335 |
| 50% | ~58s | 31 h | ~$465 |
| 75% | ~74s | 40 h | ~$600 |

Plus however long an annotator spends in the guidelines, which they open from
the item rather than reading up front. There is no per-annotator cap, so how long one person works is however long they keep
going: at the middle estimate, 250 items is about four hours.

**The failure rate is the whole estimate and nobody knows it yet.** Run 100
items first and read it off; that also tells you whether the six mechanisms are
learnable before the budget is committed.

## Phases

`annotation`, and nothing else. Annotators land on item one.

No consent phase: internal, IRB-exempt lab annotation by lab members, per the
brief.

**No training phase.** Annotators are calibrated before they get the link, so a
graded practice round would gate people who have already been trained. Removed
rather than disabled — a `training` phase named in `order` with no `training:`
block is dropped at boot with one ERROR line, which reads like a broken config.
Both halves have to come back together if a practice round is ever wanted.

**No instructions phase either: the guidelines *are* the instructions.** There
were two instructions pages, and then a **Guidelines** button opening a
generated page in a second tab. Both are gone. The whole of `guidelines.md`
renders into the banner Potato already puts above every argument, so the
definitions are on the same page as the argument they are needed for — no tab
to find, no page to have read twice, nothing to keep in sync.

`guidelines.md` is the research team's document and the only place the six
mechanism definitions live. `scripts/serve.py` reads it at boot and hands it to
Potato as `annotation_instructions`; nothing rewrites a sentence of it. It was
translated once, for this tool rather than for a spreadsheet — "for each row"
became "for each argument", the column table became the list of questions on
screen, and the Sensible Test row that read the opposite way from §2 became the
one yes/no verdict, with the three checks printed above it to be read, which is
what the tool actually asks. The definitions, the
examples, the justification rules and the checklist are word for word what they
were.

Three mechanics behind it, all measured:

- **Tables rendered by `serve.py`.** Potato's markdown renderer has no table
  support, and `guidelines.md` has three (the task overview, the mechanisms and
  the rules), written as markdown pipe tables. `serve.py` renders each table
  itself, runs everything between them through Potato's own markdown renderer,
  and hands Potato the result as HTML, which Potato sanitizes and passes
  through. `guidelines.md` stays plain markdown; do not put raw HTML in it.
- **It starts shut.** Potato's template hardcodes `open` on the banner, which
  with a whole document inside it would put three screens of prose above the
  work on every item. `serve.py` injects a `custom_footer_html` script that
  shuts it and remembers whoever opens it, per browser. `walk_study.py` asserts
  it is shut and that all six mechanisms are in it.
- **It scrolls inside itself**, 60vh, so opening it does not push the argument
  off the screen.

`annotation_instructions` takes inline text and not a filename, which is why
this goes through `serve.py` rather than through `config.yaml`: the alternative
is pasting a copy of the definitions into the config, and two copies of a
definition is one copy that is wrong. `serve.py` also warns at boot if the six
mechanism names in the document stop matching the six labels in `config.yaml`,
or if one of the three check sentences has drifted from the question on screen.

## Keyboard

Every scheme that supports shortcuts has them, and `potato preview` reports no
conflicts.

| | |
|---|---|
| Sensible Test | `Q` Yes / `A` No |
| Mechanisms | `1`–`6`, in the order listed |
| Flag for discussion | `F` |

Explicit `key_value` throughout rather than `sequential_key_binding`, which
allocates from one sequence across all schemes and would put the mechanisms'
keys wherever the yes/no question left off. `Q`/`A` puts Yes on the top row and
No on the home row, under the same finger.

---

## Assumptions

Things decided here because the brief did not settle them. Each is a place to
push back.

1. **The Rhetorical Wrapper Check is answered from prose, not from the image.**
   The question asks whether the *visual format* is a psychological trap, and
   the annotator cannot see the visual. They have the `Visual element` children,
   which sometimes describe the layout ("two panels", "a chart") and sometimes
   only describe who is in the picture. The guidelines page therefore says, in
   a tool note beside the check's own wording: judge only the format the
   descriptions actually attest to, and answer **No** when they attest to none.
   This makes the check conservative and makes its recall depend on how the
   upstream `element` extraction was written.

   **Raised and kept as it is.** The check is the research team's — it is the
   third question of the Sensible Test in `guidelines.md` — and it stays a
   yes/no with no third option, so no schema change is needed. What is still
   worth doing, cheaply, is the calibration: pull 30 wrapper-check Yes answers
   from the pilot and see whether the descriptions supported them.

2. ~~**The six mechanism definitions are drafts.**~~ **Settled.** The research
   team supplied `guidelines.md`, and its definitions, examples and rules are
   what annotators now read; the drafts written from the question wording are
   gone, along with the instructions pages that held them. The build refuses to
   write a guidelines page whose mechanism names disagree with the config's
   labels, so the two cannot drift apart silently.

   What the drafts had and `guidelines.md` did not is a **tie-break rule** for
   two mechanisms that both fit. That question is now moot: the mechanisms are
   flat, so two mechanisms that both fit are both ticked and there is nothing
   to break a tie between. What replaced it is a different judgement — *is this
   second mechanism doing work, or am I reading the whole meme?* — and section
   6 of `guidelines.md` is where that is answered. Section 5 (*Rules*, a table
   of rule, advice and example) is still the cheapest place to sharpen it
   after the pilot.

3. **`attack` arguments read in the opposite direction.** 19 of 1,621 have
   `relation: attack`, where the children are used to make the reader *reject*
   the claim. Each argument carries a coloured banner saying which, and the
   instructions say to read every question as "accept" for support and "reject"
   for attack. The alternative — excluding the 19 — was not taken because they
   are annotatable; if 19 items are not worth the extra rule, drop them.

4. **Instance ids are not what the brief specified, because that form is not
   unique.** See *The data* below.

5. **The corpus is served meme by meme in source order, each meme bottom up,
   and who does what is decided by people, written down, and shown rather than
   enforced.** Covered under *Assignment*. A stretch handed out on the
   console's Assign tab is what the annotator sees in their band and what the
   console's coverage counts; nothing enforces coverage and nothing prevents
   two people doing the same stretch, and both are visible in the console.

6. **No in-task quality control is running**, by decision: no practice round,
   no gold standards, no post-study survey, and no attention checks. Prior
   training is the whole gate, and it happens outside this tool, so nothing
   here measures whether an annotator is still calibrated on argument 400. The
   `flag_for_discussion` checkbox, Notes, and whatever stretches you
   deliberately give to two people are the only signals.

   Attention checks were built and switched off and are now removed — the
   `attention_checks` block, `data/attention_checks.json` and the console tab
   that wrote them. They were verified working before they went: the item
   rendered through the normal display path and answering it as instructed was
   recorded as `passed: true`. Putting them back means the config block and an
   items file together, and every check item has to answer every required
   scheme *including the ones behind the gate*, or it dead-ends the annotator
   on a question they cannot guess.

7. **Nobody's shift is bounded — it is only drawn.** There is no per-annotator
   cap, and there cannot be one without breaking the numbering: a cap hands out
   a contiguous block and the block's first item stops being argument 1. What
   an assignment does instead is *show* somebody where their stretch ends —
   the band, the arrows stopping, the count of what is left — while leaving
   every argument reachable and saveable. A stretch is a statement about what
   was asked for, not a fence.

## Running it: the annotation tool backend

`scripts/annotation_backend.py` is a second small server, loopback by default,
for the jobs Potato's own `/admin` dashboard does not do, or does not do
readably. It is not a replacement for that dashboard: the live view of the
study running — who is logged in, how long each argument is taking, the
behavioural and integrity reports — is still there and is not duplicated here.

| Tab | What it does |
|---|---|
| Assign | the form that hands a stretch out, optionally widened to whole memes, and who has which stretch, with what they have done, what is left and what fell outside it. The one thing on the page that writes — see below |
| Progress | the corpus three ways: how much of it is finished and by how many people (finished by two is what agreement has to work with), how much of it is assigned and how much doubly assigned, and what the work itself came to — annotations, verdicts, drafts, flags, export problems |
| Download | `annotations.csv`, `annotations.jsonl`, `validation_report.json`, `flags.csv`, `agreement.json`, a zip of `annotation_output/`; and **Who is where** — per annotator, what they have finished as ranges, what they were assigned, what is left, and how many drafts and flags |
| Annotations | every annotation, grouped under the argument it is about, two annotators side by side, filterable by annotator, verdict, a mechanism ticked, argument number or free text, and downloadable as the filter leaves it. Two annotators *differ* when their verdicts differ or the sets of mechanisms they ticked are not the same |
| Agreement | one pair over one stretch of argument numbers: Cohen's κ on the Sensible Test, the five disagreement categories, findings in sentences, a map of the range, per-mechanism agreement, the commonest swaps, and every non-identical argument — see below |
| Flags | every flagged argument, with the argument itself, the verdict, the mechanisms ticked and their justifications |

### Why agreement is computed here and not read off `/admin/iaa`

`/admin/iaa` is restricted to items whose
per-item annotator cap is 2 or more, and **this study sets no cap**: work is
handed out by argument number, between people (see *Nobody's shift is
bounded*). So that page reports `null` for every coefficient however much two
annotators have actually read the same arguments. `scripts/agreement.py`
computes over the overlap that is genuinely on disk — any argument two or more
people have annotated — which is the only overlap this study can have. It
shares no code with Potato's version: a 2×2 Cohen's κ is six lines, and the
dependency bought nothing once the coefficients it supplied were dropped.

Two consequences are deliberate. Scope is per question, not per page: an
argument both annotated but only one of them answered the Sensible Test on
drops out of the Pass/Fail numbers and stays in the mechanism numbers, because
they did answer the mechanism question. And κ is `—` where it is undefined —
no arguments, or one verdict being all either of them ever used — because
there is nothing there for a chance correction to correct.

**The page is one pair at a time.** The old one put every annotator's
coefficients in one table, which reads as a scoreboard and answers nothing: two
people are either reading the codebook the same way or they are not, and which
two is the whole question. The pair and the argument range are both in the URL,
so the view is a link you can send to the two people it is about.

**The findings are templates, not prose.** Six rules in priority order, the
first four that fire are shown, each a fixed lead sentence and a fixed sentence
of numbers. Nothing is generated and no model is involved: a rule either clears
its threshold on this pair and this range or it prints nothing. The thresholds
live in `findings()` in `scripts/agreement.py` and nowhere else, and under 20
shared arguments the block is hidden outright — a rule that fires on three
arguments is a coincidence with a sentence round it.

`tests/test_agreement.py` is what holds all of this still. The two Week 2 pairs
in `tests/fixtures/` are checked end to end — every count, every coefficient,
every per-mechanism number and the findings in order — against the numbers the
research team read off their own report, so the tests are a specification and
not a snapshot. Run them with `python -m unittest discover tests`.

**It changes one file, and only that one.** No config is rewritten and nothing
is sent to the study server: the Assign tab writes
`annotation_output/assignments.json`, which the study server re-reads on its
next page render — so a stretch handed out here reaches the annotator's band
without a restart, and deleting the file puts everybody back on the whole
corpus. Everything else on the page is read-only and computed from disk through
`scripts/export_annotations.py` rather than a second copy of its logic, which is
why the console still works with the study server stopped. That is when you want
the backup.

The Assign tab is not the one that used to be here. The old one set
per-annotator caps through Potato's admin API and wrote a default cap into
`config.yaml` — that is the machinery that breaks the numbering, and it is
still gone, along with the **Attention checks** tab that wrote a config block.
What is here now writes one file that Potato never sees, and the meme-boundary
snapping is the one thing carried over from the old tab: a meme's arguments are
read bottom up as a unit, and half of one is half the context.

**Who is where** carries the other half of the picture. `Completed` is every
argument somebody has finished, as ranges, so a skipped stretch shows as a gap;
`Assigned` and `Left` are what they were given and how much of it is
outstanding; and `Drafts` is how many arguments they left part-answered — work
that is not finished and that `--strict` will refuse.

## Items with nowhere to go

The brief asked whether any cases still have no home. Three:

- **An argument whose `Visual element` children are too thin to answer the
  Rhetorical Wrapper Check on.** It is not a Yes and it is not honestly a No.
  The guidelines route it to No + flag + a note saying what was missing, so it
  is recoverable, but it is recorded as a No and will read as one in the data.
  A third option was considered and not taken: the check is the researchers'
  three-way Sensible Test and stays yes/no. The flags queue in the study
  console is where these surface.
- ~~**A failing argument where two mechanisms are exactly equally
  load-bearing.**~~ **Settled by flattening.** This had no home while the task
  asked which mechanism was the primary one: for a genuine tie the rule
  restated the question, the annotator picked one, and two annotators who saw
  the argument identically could still be recorded as disagreeing. Both are
  ticked now and the question is not asked. What is left is the softer call of
  whether a second mechanism is doing work at all, which `guidelines.md` §6
  addresses and which the agreement report separates out: it lands in *overlap,
  one has extra* rather than in *no shared mechanism*, and those are two
  different problems with two different fixes.
- **An argument whose children plainly belong to a different claim** — an
  upstream extraction error. There is no "this data is wrong" answer. Flag plus
  a note is the only route, and somebody has to read the flags.

## The data

`scripts/build_instances.py`, rerunnable, writes `data/instances.json` and
`data/manifest.json`.

**The id form in the brief is not unique.** `{image_stem}::{claim.id}` collides
on 52 (image, claim id) pairs — the same claim argued twice from disjoint child
sets — so 1,621 arguments would collapse to 1,566 ids and Potato, which keys
items by id, would silently drop 55 arguments. The build uses the bare form
where it is unique and appends the child-id set otherwise
(`85_image_batch_2::7::3-5-13-14`). Both are pure functions of the argument's
own content, which is what makes them survive a rebuild.

**One pair needed a third tier, and it is a data bug.** In
`142_image_batch_2`, claim #0 with child #1 appears twice — once as `support`
and once as `attack`. The same edge cannot be both. The build disambiguates with
the relation and records it in the manifest under
`contradictory_relation_groups`. Fix it upstream.

**Every instance carries its number.** `source_index` (0-based) is its line in
`args_0911.json`; `item_number` (1-based) is its position in the served reading
order. They differ, because each meme is turned bottom up. `item_number` is
rendered into the argument's HTML as *Argument N of 1621* and is a column in
the export; `source_index` is a column too. Both are in the file rather than derived at read time, so nothing
downstream has to know how the order was arrived at.

**The manifest diffs itself against the previous build.** It stores every id and
the source hash; a rebuild prints added and removed ids and shouts about removed
ones, because a removed id is an id whose annotations are now orphaned. Run
`python scripts/build_instances.py --check` before committing a regenerated
source — it exits non-zero if any id disappeared.

## Rejected, and why

| Considered | Rejected because |
|---|---|
| `layout.grid` / `layout.groups` for a three-column form | It breaks `display_logic` — the whole conditional half of the task. See *Potato defects* |
| `require_fully_annotated: true` | It makes unanswerable schemes required and dead-ends the annotator. See *Potato defects* |
| `batch_assignment` for whole-meme overlap | Needs the full annotator roster in the config; incompatible with open registration |
| `overlap_sample` for an automatic 10% agreement set | Raising a stratified sample to 3 annotators is automatic assignment, and assignment is what was given up for the numbering. Give two people the same stretch instead |
| A per-annotator cap, in config or from a console | A cap hands out a contiguous block, so the block's first item is no longer argument 1 and the whole numbering stops meaning anything. The assignment overlay is the answer instead: it draws the stretch without fencing it |
| Blocking an argument outside somebody's assigned stretch | A stretch gets extended verbally mid-shift, and an annotator told to keep going should not be waiting on somebody to open a console. The band says where they are and the console says what fell outside |
| Assigning a stretch automatically at first login | For a roster this size the admin knows who is doing what, and an automatic hand-out is a second thing to reason about when the numbers stop lining up |
| Scoping the navbar's own `Progress` counter to the stretch | `total_count` feeds `Go to`'s `max` as well, so scoping it would cap the box at the end of somebody's batch — and `Go to` reaching every argument is the point of the numbering. The band carries the batch-scoped numbers instead |
| Leaving `Next` as one button | Whether it would let you past was invisible until you pressed it, and a button that sometimes refuses teaches people to fill boxes to get past it. Two buttons say what the choice is |
| A `Save draft` that stays on the argument | Nobody wants to save and stay; the reason to save a draft is to move on |
| Potato's own `csv`/`jsonl` export as the deliverable | One sparse column per scheme-and-label, no item fields, no timestamps |
| `assignment_strategy: random` | There would be no number to tell anybody |
| Shuffling the 614 memes on a seed | A useless naming order: nobody could say where argument 500 was without running the build. Ordering *within* a meme costs nothing, because a meme's arguments stay together either way |
| Reversing each meme's block instead of sorting it by height | 196 (claim, child) edges run the other way in the source file; reversal alone reads those parents before their children |
| Keeping `media/guidelines.html` and the Guidelines button | A second tab to find, a build step to forget, and a copy of the definitions that can go stale. The banner is on the same page as the argument |

## Potato defects found while building this

Each was found by driving the page, and each passes `validate --strict`,
`preview`, and the boot log without a word.

1. **`layout` and `display_logic` are mutually exclusive.** With any `layout`
   block — grid alone or grid plus groups — each conditional question renders
   twice: the `<form>` is hoisted into the grid and the
   `display-logic-container` beside it is left empty. The gate toggles the empty
   div and every gated question is on screen permanently. Measured: with
   `layout`, page height 2518px whether the checks were Yes or No; without it,
   1800px shut and 2685px open. The config has no `layout` block and must not
   get one. `ui/90-BROKEN-grid-without-groups.png` and
   `ui/91-no-layout-block-gate-works.png` are the two states side by side.

2. **`require_fully_annotated: true` requires things that cannot be answered.**
   It stamps `label_requirement: {required: true}` onto every scheme lacking
   one, including `pure_display` headings (measured while there were three). The client renders no input for them
   so Next stays enabled; the server then refuses every save with `Required
   annotation(s) not completed: section_sensible_test, section_record` in a
   corner toast naming internal scheme names. The annotator is stuck on item one
   with no visible question to answer. It also silently made Notes and the
   discussion flag mandatory. Not set; every scheme declares
   `label_requirement` explicitly instead.

3. **`min_chars` is advisory.** It draws a red `3/15 min characters` counter and
   does not block anything. Typed `n/a` into a required justification, clicked
   Next, and it saved. This is why constraint 2 needs the export check.

4. **`training.annotation_schemes` is inert.** Found while the task still had a
   practice round: setting it produced a training layout byte-identical to the
   annotation one, all schemes present. Not relevant now that training is gone,
   but worth knowing before anyone adds one back.

5. **`display_logic` is not transitive.** A justification's gate reads only the
   `mechanisms` multiselect, so un-ticking the mechanism after typing leaves the
   text stored. `orphaned_justification` in the export catches it.

6. **`potato preview --screenshot` reports FAILED on a healthy task.** It waits
   for `.annotation_schema`; the form renders as `.annotation-form`. The PNG is
   fine. Judge the picture, not the exit code.

7. **The skill's `estimate_effort.py` and `walk_task.py` both break here** —
   the first on the mapping form of `num_annotators_per_item`, the second
   because it answers every radio with its first option and `mechanisms` is
   gated, so it fills the mechanism before the gate has opened and never
   exercises the branch. `scripts/walk_study.py` replaces the second.

8. **The `annotation_instructions` sanitizer strips `class` from an `<a>` and
   keeps it on a `<p>`.** A styled link in the banner therefore renders
   unstyled, with nothing said anywhere — the config validates, the page
   renders, and the link looks like body text. It cost this study a Guidelines
   button that had to be styled off its `href`; the button is gone now, but the
   rule still binds anything written into that banner.

9. **`potato.routes` is imported by the bare name `routes`.** `flask_server`
   puts the package directory on `sys.path` and does `from routes import
   configure_routes`, so `import potato.routes` builds a *second* module object
   and re-registers all 159 handlers, which fails on the first duplicate
   endpoint. `scripts/serve.py` reads `sys.modules` instead of importing it —
   and it has to patch that module after `create_app()` has run, because the
   module takes its own reference to
   `_instance_meets_required_annotation_rules` at import time and import
   happens inside `create_app`.

10. **`num_annotators_per_item` counts holders, not annotators.** An item is
   saturated by somebody merely being assigned it. With `default: 1` and an
   unset `max_annotations_per_user`, the first annotator to register is handed
   the entire corpus and every later annotator is served nothing — no error, no
   warning, and a navbar reading `0/0`.

11. **`handover.py` does not remove the accounts it says it removes.** It deletes
   each test annotator's `annotation_output/<user>/` directory and leaves their
   entry in `user_config.json`, so the login still works and the account is
   still assignable. Delete that file after running it.

## Checking it

```bash
potato validate config.yaml --strict                          # structure
potato preview config.yaml                                    # schemes + keybinding conflicts
python scripts/build_instances.py --check                     # id drift since the last build
python scripts/walk_study.py --url http://127.0.0.1:8765      # the whole study, end to end
python scripts/capture_ui.py --url http://127.0.0.1:8765      # full-page screenshots into ui/
python scripts/export_annotations.py --self-test              # the validator's own tests
python scripts/export_annotations.py --strict                 # export + constraint check
```

`walk_study.py` is the one that matters. It registers, checks the guidelines
banner is shut and carries all six mechanisms, annotates one argument per
branch, leaves a fourth part-answered and presses Next — which has to move, and
has to leave that argument reading `In Progress` — navigates **back** (never
reloads: a browser restores form state itself and a reload would pass even if
the server stored nothing), and compares `user_state.json` against what it
typed, scheme by scheme. Last run: all three branches stored correctly, the
draft advanced and read `In Progress`, a passing argument stored no mechanism
key at all, no problems.

Separately measured on 2026-09-20, driving the page as an annotator: `Go to`
500 lands on `Argument 500 of 1621` with `Instance #500` in the navbar; the
progress counter goes to 1/1621 on a finished argument and stays there when the
next one is left as a draft; and the back arrow returns to the argument before,
whether or not it is finished.
