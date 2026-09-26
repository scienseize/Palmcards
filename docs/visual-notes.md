# Visual notes for the polish pass

Severity: **B** blocker (can't read or use it, or it covers the face or hands) · **S** should (works, looks off or confusing) · **N** nice (polish only).
The polish pass changes only `render.py` and `style.py`. Items marked **[behaviour]** need a code change elsewhere and go on the separate list at the bottom.

Screenshots reviewed: 01 browse word, 02 browse sentence, 03 browse paragraph, 04 options ring, 05 tone dial, 07 length stretch, 09 rehearse, 10 review. Still to capture: 06 marks, 08 count-in and calibration, 11 review focused, 12 tutorial, 13 alerts and hints, 14 high contrast.

---

## Direction

- **Feel:** like Kat's demo. The face comes first; the text is a calm overlay beside it; very little chrome (few boxes, few borders).
- **Keep:** the monospace text, orange as the one "current" colour, the dimmed-vs-highlighted sentence, fingertip dots, the ring's curved yellow connectors.
- **Biggest problem:** the notes and their dark backing sit over the face on almost every screen.

## Screen layout (the rule the fixes follow)

The camera frame is split into three vertical zones, because you sit in the middle with your right hand on the right:

```
| 6% |  TEXT COLUMN (6%–38%)  |   FACE ZONE (38%–55%)   |   HAND ZONE (55%–95%)   |
|    | labels, notes, ring,   |   nothing drawn here    |   hand box, command     |
|    | gauge, review metrics  |   except fingertip dots |   zone, zone hints      |
```

- Everything the user reads lives in the text column. Nothing is drawn over the face zone.
- The hand zone only holds what belongs to the hand (box corners, command zone, its hints).
- Status pills stay bottom-left; performance numbers are hidden unless debugging.

---

## Every screen

- **B** Text column is too wide: lines run to ~48% of the frame and cross the eyes, nose and mouth ("felt", "project", "heads are very forgiving"). Limit it to the text column above (wrap sooner; the font can drop one size if needed).
- **B** The soft dark backing panel is a big blurred rectangle that also darkens half of the face (clearest on 03 and 09). Replace it with a tight backing per line of text (or a text shadow/outline), so the video stays visible between lines and nothing shadows the face.
- **S** Performance readout (`cam 30.2 shown 31.6 fps hands 21.0 ms work 4.4 m`) is always on, is clipped at the right edge, and collides with the Review metrics. Hide it by default; show it with a debug key.
- **S** Labels sit in heavy black boxes of different widths, and line 2 mixes state and gesture hints ("SENTENCE TONE: WARM / PINCH + LIFT: ASK FOR A REWRITE"), so it runs long and into the hand box corner (05, 07, 10). Use Kat's style: orange text with a subtle shadow, no box. Line 1 = state, line 2 = operation (dimmer), line 3 = gesture hint (smallest, dimmest). No line wider than the text column.
- **S** Hand box corners are large and bright and overlap label line 2 on 07. Make them smaller and fainter (about 30% opacity), and never draw them over text.
- **S** The last visible line is cut through the middle of its letters ("Most of us rehearse in our heads" on 01, 02, 03). Fade the last line out, or show only whole lines.
- **N** The mouse pointer shows in the middle of the frame. Hide it while the window is focused. **[behaviour]**
- **N** Bottom-left pills (CLOUD LLM, H: KEYS) are fine; make them a little dimmer so they don't compete with the label.

## 01 Prepare, browse by word
- **S** Focused word as an orange pill with dark text works well. Keep it.
- **B** Covered by the text-column fix (the line runs across the face).

## 02 Prepare, browse by sentence
- Orange sentence, grey rest: works. Only the every-screen fixes apply.

## 03 Prepare, browse by paragraph
- **B** The backing panel darkens a wide band across the face.
- **N** A whole paragraph in orange text is loud. Consider Kat's version (orange fill behind the paragraph, dark text) or a slightly less saturated orange for paragraph-level highlight, and compare.

## 04 Focus by word, options ring
- **S** The focused word appears twice: an enlarged "challenge:" block over the line, and the orange node at the top of the ring. Follow Kat: the preview stays inline in the sentence (orange, normal size), and the 12 o'clock slot shows the empty dark box for the moment the word moves in.
- **S** Other text is dimmed only slightly, so the ring, connectors and sentence text all fight (e.g. "hear it" on top of "We started"). Dim non-focused text much further while the ring is open (Kat's is barely visible).
- **S** The ring is lopsided and its left nodes touch the frame edge ("hear it" at x≈60). Keep the whole ring inside the text column, centred on the word where it fits.
- **S** Every alternative keeps the word's trailing colon ("idea:", "premise:", label `"challenge:"`). Show bare words in nodes and the label, and reattach the punctuation on commit. **[behaviour]**
- **S** No **stress** node visible. Check whether it's missing or placed off-frame. **[behaviour, if missing]**

## 05 Focus by sentence, tone dial
- **B** The gauge sits on the face (over the cheek, next to the eye). Put it just right of the text inside the text column.
- **S** The gauge ends aren't labelled. Add small "formal" (top, blue) and "conversational" (bottom, orange) labels.
- **N** The enlarged sentence reads well. Keep it.

## 07 Focus by paragraph, length stretch
- **S** The line between the index tips is drawn on top of the notes and crosses "everyone.". Draw it under the text and more transparently.
- **S** "PARAGRAPH LENGTH: SAME x0.98" is jargon. Show it as "SAME", "SHORTER −20%" or "FULLER +30%".
- **S** Label line 2 is overlapped by the hand box corner (see the every-screen label fix).

## 09 Rehearse with follow
- **S** The command zone is a full rectangle with a line cutting across the background; it reads like a second window. Use corner marks like the hand box.
- **S** Its two hints are separate boxes. One small, dim line each, no boxes.
- **N** REC indicator is clear. Keep it.
- **B** Text over the mouth (covered by the text-column fix).

## 10 Review, browse
- **B** The metrics table sits in the hand zone: the hand covers it, the hand box runs through it, and its last row ("SHOULDERS TILTED") collides with the performance readout. Move it into the text column (below the notes), or show it only on demand.
- **S** Label line 2 repeats the table ("TAKE 1: 9/9 SPOKEN, 216 WPM, 0 FILLERS/MIN, PITCH RANGE 8.6 ST / RAISE A FIST: NEW TAKE") and runs across the frame. Keep "TAKE 1 · 9/9 SPOKEN" there; the numbers belong in the table only.
- **S** "PITCH RANGE ST" means nothing to a new user. Use "VOICE RANGE" with the unit spelled out ("8.6 semitones"), or leave it out of the summary.
- **N** The table's orange "TAKE 1" header and white rows are fine once it's moved.

---

## Behaviour notes (not for the polish pass)

- Strip punctuation from options-ring alternatives and the label; reattach on commit (04).
- Check the stress node in the options ring (04).
- Hide the mouse pointer over the window.
- A debug key to show or hide the performance readout (the drawing is in `render.py`; the key belongs in the command handling). The readout is now off by default (`DEBUG.show_stats = False` in `style.py`); the key only needs to flip it. Until then the bench scripts' on-screen line is hidden too.
- Review label line 2 repeats the table: keep "TAKE 1 · 9/9 SPOKEN" and leave the numbers to the table (10). Built in `main.py` (`Takes.on_transcribed`, `last_saved`).
- "PITCH RANGE ST" in the take table: "VOICE RANGE" with the unit spelled out (semitones), or left out (10). Row names are `METRIC_ROWS` in `review.py`.
