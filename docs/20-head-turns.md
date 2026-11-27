# How heads turn

Status: 2026-11-27 (the nose moves on its own, below). Head turns up to ±30 (about 45° on screen) are built
(`studio/refine.py`) and the owner judged them "a massive improvement".
Turns to the side profile (90°) are planned, not built, for flat (2D) characters: they need drawn side
artwork. **3D (VRM) avatars turn to a profile already** (2026-11-21, D-062): they have a real head to turn,
so the wide head turn (`head_turn`) takes them to 90 degrees, with the shoulders following.

## What was wrong, and the rules we follow now

The owner saw the head slide sideways, leaving empty space or a skin-
coloured patch. Measured with `studio/reveal.py`: the rigger's body turn
slid the whole head 40 px; our head turn moved the eyes, nose and mouth
unchanged inside an outline that never moved (both eyes stayed 26.4 px
wide at every angle), which reads as a shift, not a turn. Later the owner
noticed the head looked concave at a strong turn: the face was squeezed
against that fixed outline.

The rules now, each taken from how Live2D artists rig a head (Live2D's
"Adding XY facial movement" tutorial, and the 90° tutorial below):

| Rule | Where |
|---|---|
| Each part has its own deformation: eyes, brows, mouth and nose follow small grids so they keep their shape; face and hair follow the head's surface point by point | `refine.offsets_for`, `grid_offsets` |
| "Thinner in the back, wider in the front": the head is a rounded solid turned about its middle, so the far eye narrows (22 px at full turn on Alice) and the near eye widens (29 px) | `refine.turned` |
| The head turns about the neck, behind its middle: the whole head, outline included, shifts a little with the turn (about 5 px), the face most, so the far side stays round | `TURN_AXIS`, `NOD_AXIS` |
| Parts in front move more, parts behind move the other way: the fringe and nose stand out; hair behind the head moves against the turn | `DEPTH`, `BACK_DEPTH` |
| Hair continues behind the face, hidden at rest, in the hair's shadow tone, so a turning face uncovers hair rather than background | `partsplit` (`hair_back`) |
| The neck under the face tapers like a neck, so no square corners show when the chin moves | `partsplit.neck_column` |
| The body turns as a cylinder about the spine, fitted to the body's width at each height; the head and neck ride on the spine; everything follows one smooth field, so overlapping parts move alike | `refine.refine_body` |
| Long hair below the chin keeps hanging while the head turns | `turned` (hang) |
| The nose stands out: it goes further than the eyes, and ends a turn as far to that side of the eyes' middle either way, wherever it was drawn; a nose drawn on the skin is found on the face's picture | `find_nose`, `nose_shift` |

Results on Alice at full turn or full body turn, against the earlier
version: head moved by the body turn 40 → 6.5 px; uncovered area at a body
turn 4,360 → 1,468 px²; new gaps 644 → 68 px²; made-up filler shown 7,328
→ 1,468 px². On ten other test characters head turns and nods leave
almost no new gaps (0–260 px²).

Not yet right: at the very end of the range (±100%) the near-side hair
still stretches somewhat, and pictures whose hair edges keep a light halo
from background removal (t3) show it more when the head turns. Up to
±50%, the range normal conversation uses, turns are clean.

### 2026-11-27: the nose moves on its own (the right turn mirrors the left)

The owner saw Alice's turn to the right look less like a turn than her
turn to the left, which looked good. A first attempt sized the head's
rounded surface by the face instead of by the whole head, hair included:
every turn then read as a turn, but the eyes were squeezed and stretched
too much, and the owner judged the earlier look better, the whole head
turning, not just the face. That attempt is undone; the rig is as before
except for the nose.

The owner suggested comparing the left turn, mirrored, with the right
turn. Done on a copy of the rig with every part one flat colour (shapes
only), the two matched to within 2 to 4 pixels in 520: the movement was
already the same both ways, the drawing was not. Alice's nose is drawn on
the face's skin, not as a part of its own, a little to the left of the
eyes' middle (0.07 of the face's half-width), and it moved exactly with
the eyes. At a full turn left it sat well to the side of the face's middle
(0.16 of the half-width); at a full turn right hardly at all (0.06). It led
the turn to the left and lagged behind the turn to the right, so that turn
looked like the face sliding.

Now a nose drawn on the face's skin is found on the face's picture (the
darkest small mark between the eyes and the mouth, near the eyes' middle;
`refine.find_nose`), and the skin round it follows it (`nose_shift`,
`NOSE_REACH`). The nose stands out from the face, so it goes further than
the eyes, and at the end of a turn it sits the same distance to that side
of the eyes' middle whichever way the head turns: on Alice 0.095 of the
face's half-width either way (it was 0.08 left and 0.05 left). The left
turn is practically unchanged. A nose drawn well to one side is evened out
only partly (`NOSE_EVEN`), so it never moves against a turn: a face drawn
half-turned keeps some of its own turn, which only a drawing can undo. A
nose that is its own part is evened out the same way.

On the eleven test characters a nose was found on six (Alice, f1, f2, f3,
t2, t3); on the other five there is no clear mark and nothing changes.
f1, f3 and t3 have noses drawn 0.11 to 0.23 of the half-width to one side;
their weaker turn now goes the right way or nearly, instead of lagging.
Tests: `NoseTest` in `studio/test_refine.py`, on a hand-drawn test face.
Characters keep the rig they were made with: make them again in Character
Studio to get this.

## What the 90° tutorial adds

Source: Merely.wufeii, "[Live2D Tutorial] 180°(90°) head turn rigging"
(https://www.youtube.com/watch?v=-mLVKeRwuZk, 2024). The owner supplied a
timestamped breakdown made with Gemini; this is our summary of the
techniques, in our words. The creator says a ±30 turn (about 45°) is
enough for everyday use and the 90° profile is an advanced extra.

1. **New artwork, drawn for the profile**: a side nose (the bridge and tip
   that stick out), upper and lower side lips with fills under them, and a
   whole-profile fill behind the face, plus a profile sketch used only as
   a target while rigging.
2. **A wider turn control**: Angle X goes to ±60 with keys at ±30, ±50
   (where face trackers tend to rest) and ±60.
3. **One shared head deformer does most of the work**, but parts near its
   squeezed edge (side mouth, nose, hair, ears, expression overlays) are
   taken out of it once the corner poses exist, keeping the same angle
   keys, because inside it they stretch and snap back. Our `refine.py`
   already works this way: each part's movement is computed directly, not
   through a parent deformer, so that stretching cannot happen.
4. **Turn and nod together at the profile**: the usual corner rule (turn
   change plus nod change) shears a profile instead of tilting it, so the
   creator copies the tilt from the ±30 diagonal onto the profile, partly:
   corner(−60, ±30) = profile(−60) + α·[pose(−30, ±30) − pose(−30, 0)].
   Our additive controls give the usual rule, which is right up to ±30.
5. **Hair at the profile**: split the front hair into left and right
   halves; the half on the side turning away is drawn lower (behind) as
   the head turns; inverse masks stop hair covering the side nose; where a
   strand gets too thin, its shape is taken from a smaller angle.
6. **Ears** move behind the face, inverse-clipped to the hair so they still
   show in front of it; the bald head underneath is pulled in so it does
   not poke through.
7. **The mouth** at the profile needs its own opening movement, with room
   between the lower lip's points so it does not fold.
8. **Trackers**: at the profile a nod is seen as a tilt, so the nod and
   tilt controls are blended according to how far the head is turned.
   The device has no face tracker; its gesture engine should simply not
   nod at a full profile, or use the same blend.
9. **Physics** input ranges are widened to the new turn range.

## What this means for us

**For ±30, next:**
- Split the front hair into left and right halves and let the far half
  drop behind the face at strong turns. This needs draw-order changes by
  angle, which Inox2D and the Live2D format both support but our converter
  does not yet (it refuses draw-order bindings).
- Keep hair strands from getting too thin at the end of the range.
- Clean the light halo some background removals leave on hair edges.

**For 90° (with the drawing workflow):**
- Generate the profile artwork for each character with an image model
  (side nose, side lips, the fill behind the profile), placed so it is
  hidden at rest; candidates are in the owner's research notes (Qwen Image
  Edit multiple angles, THA3 as a reference for fitting).
- Add the ±50 and ±60 keys, the profile corners by copying the tilt, the
  hair split with draw-order changes, inverse masks and the mouth fixes
  above.
- Players must handle masks (including inverse masks) and draw-order
  changes by angle. Ayagami does; the device's own drawing code will need
  them when it plays Live2D-format characters, and our converter must
  carry draw-order changes and masks across.
