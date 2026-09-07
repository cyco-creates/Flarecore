# Tracking and obstruction

## A sun behind trees

1. Choose **Track bright sources → Solve entire clip**.
2. Use Recommended settings. This solver follows luminous cores, not foreground
   camera motion. Place the picker near your target and narrow the search radius
   if the scene contains competing lights.
3. In Source tuning choose **Image + depth**, or **Image visibility** without a
   depth map. Start with source radius **0.03**, visibility smoothing **0.3**.
4. Render the whole shot, including at least one unobstructed view. Check the
   flare-only output and the measured visibility / tracking-confidence summary.
5. Bake the path only after inspection. Hidden positions are estimates, not
   measurements; correct them manually when necessary.

The radius describes the emitter's footprint, not the size of the flare halo.
It is a fraction of image height. Too large a radius includes unrelated sky or
buildings; too small a radius misses partial cover around the source.

## What changed

- Source tracking groups connected bright regions and locates their luminous
  cores. Clipped plateaus no longer supply many arbitrary rim pixels.
- Whole-clip solving has a missing-observation state. It interpolates hidden
  intervals between trusted positions and bounds extrapolation at shot ends.
  Persistent nearby observations can reacquire a smaller source.
- Frame matching also uses the new source-region candidates. Dot mattes retain
  their specialized detector. Scene anchoring rejects large foreground-parallax
  disagreement with a stable source; full-clip solving does not use it.
- Feature tracking stops following frame-to-frame texture after losing its
  feature. It must reacquire the original template before resuming. A featureless
  saturated sun is still better handled by source tracking than a point tracker.
- Image visibility measures linear-light flux over the source aperture against
  a clear-view reference, subtracting an estimated surrounding halo/sky pedestal.
  The estimate controls both brightness and the existing obstruction-size response.
- Hybrid uses the stronger of image and depth obstruction without multiplying
  the two measurements of the same branch. Depth coverage now uses dense sampling
  without the old 15% coverage dead zone, so narrow foreground objects contribute.
- Visibility is measured at the physical source position before artistic travel
  damping. Frame-matching smoothing does not move the photometry aperture.

## Choosing obstruction mode

| Mode | Use |
| --- | --- |
| Image + depth | Default. Image evidence supplements depth that misses thin branches. |
| Image visibility | No depth model needed; useful for visible emitters in footage. |
| Depth only (legacy) | Depth controls visibility; retains the old hold/fade behavior. Coverage sampling is improved. |
| Off | No automatic image/depth obstruction; supplied per-light obstruction is still honored. |

Use modest depth blur and temporal smoothing for foliage. Heavy conditioning
can remove narrow branches before Flarecore receives the map. Image evidence
helps, but cannot recover information missing from both inputs.

## Limits and compatibility

Image visibility is an estimate, not a physical reconstruction. It responds to
exposure changes as well as cover. It needs a clear reference within the batch;
if the source is hidden throughout, use a depth map or authored light intensity.
Off-screen and completely dark reference apertures are treated as unknown.
An emissive foreground object can confuse image-only visibility; use depth or a
mask for such shots. Solve separate shots separately: no cut detection is implied.

Node IDs and existing widget order are unchanged; the visibility selector is
appended. Existing workflows use Hybrid when no explicit value is supplied.
Choose Depth only to disable image-driven attenuation. Feature confidence is
reported separately: confidence alone does not prove physical obstruction.

Restart ComfyUI and refresh its page after updating. Saved Manual placement stays
Manual; switch to the full-clip tracking method explicitly for moving sources.
