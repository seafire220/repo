# How heads turn

Status: 2026-11-27 (the head sized by the face, below). Head turns up to ±30 (about 45° on screen) are built
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

Results on Alice at full turn or full body turn, against the earlier
version: head moved by the body turn 40 → 6.5 px; uncovered area at a body
turn 4,360 → 1,468 px²; new gaps 644 → 68 px²; made-up filler shown 7,328
→ 1,468 px². On ten other test characters head turns and nods leave
almost no new gaps (0–260 px²).

Not yet right: at the very end of the range (±100%) the near-side hair
still stretches somewhat, and pictures whose hair edges keep a light halo
from background removal (t3) show it more when the head turns. Up to
±50%, the range normal conversation uses, turns are clean.

### 2026-11-27: the head sized by the face (a turn, not a slide)

The owner saw Alice's eyes and features look "off" turning one way, as
with the earlier concave look: they should take the shape a turning head
gives them. Measured (`turned`'s output, on all eleven test characters),
the turn was symmetric, but on every character the face slid more than it
turned: the eyes moved only 1.0 to 1.3 times as far as the cheeks, and the
gap between the far eye and its cheek hardly changed. The head's rounded
surface had been sized by everything on the head, hair included, so with
big hair it was 1.2 to 1.9 times as wide as the face; the cheeks lay in its
middle and moved almost as much as the eyes. Lopsided drawings (hair longer
on one side) made one direction look worse.

Now the surface is the face's (1.03 times its half-width), so the cheeks
lie on its outline and hold, and the features are not held at the outline
(only the face's and the hair's own outlines are, which would uncover what
is behind them): they go round the surface, so the far eye narrows and
nears its cheek and cannot pass it, and the near eye widens. The turn's
angle is 22 degrees (was 18) to keep the movement clear on the smaller
surface. On the eleven characters the eyes now move 2.0 to 3.7 times as
far as the cheeks (f1, whose face part is unusually wide, 1.1 to 1.4), the
far eye narrows to 0.5 to 0.9 of its width and the near one widens to 1.0
to 1.6; Alice's face no longer uncovers the made-up fill at the jaw on a
turn, and t3's and t5's doubled outlines are gone. A test checks a face
with hair three times its width turns rather than slides
(`test_a_face_with_big_hair_turns_instead_of_sliding`). The jaw line on
the near side can stretch slightly where the held cheek meets the turning
skin. Characters keep the rig they were made with: make them again in
Character Studio to get this.

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
