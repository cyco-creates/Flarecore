# Tracking and visibility

Use this guide when a flare needs to follow a moving source or react to
objects passing in front of it.

## Sun behind trees

1. Choose **Track bright sources → Solve entire clip**.
2. Place the picker near the source and use **Recommended settings**. Narrow
   the search radius if there are competing bright areas.
3. In Source tuning choose **Image + depth**, or **Image visibility** if you do
   not have a depth map. Start with source radius **0.03** and visibility
   smoothing **0.3**.
4. Render the complete clip, including at least one clear view of the source.
5. Inspect the flare-only output and tracking-confidence message before baking
   the result to a path.

Hidden positions are estimates. Correct them with the picker or an authored
path when the shot needs exact control.

## Choosing a tracking method

- **Match between frames** is best for a visible bright source with moderate
  motion.
- **Solve entire clip** is best for a sun or lamp that may disappear behind
  foliage, buildings or other objects.
- **Track a dot matte** gives each white dot on a black control plate its own
  source identity.
- **Track a chosen feature** is best for a textured edge or detail when
  brightness detection is unreliable. A featureless, clipped sun has nothing
  useful to match.
- **Follow camera motion** is best for an off-screen or diffuse source that
  should stay fixed in the scene while the camera moves.
- **Draw / edit a path** gives full manual control.

## Choosing visibility mode

| Mode | Use |
| --- | --- |
| Image + depth | Recommended. Combines measured source brightness with a depth map. |
| Image visibility | Use when no depth map is available. |
| Depth only | Use when the depth map should control obstruction by itself. |
| Off | Disable automatic obstruction. |

Image visibility measures how much of the source area remains visible. It can
dim and shrink the flare as branches cross the source. It needs a clear view of
the source somewhere in the batch and can also react to exposure changes.

The source radius is the size of the measured emitter area, not the size of the
flare halo. It is a fraction of image height. Increase it for a larger source
or softer partial cover; decrease it when nearby bright objects are being
included.

For depth, use a consistent near/far convention and moderate blur. If the sky
or source is incorrectly treated as foreground, adjust `light_depth` or
`invert_depth`. For video, batch normalization and temporal smoothing usually
give steadier results than normalizing every frame separately.

## Practical limits

No tracker can recover a source that is hidden for the entire clip without
additional information. Image visibility is an estimate, not a physical
reconstruction, and exposure changes can look like obstruction. An emissive
foreground object can confuse image-only visibility; use depth or a mask for
those shots.

Always check the flare-only output before final compositing. Use **travel** to
reduce unwanted source movement without changing the underlying track, and bake
to a path only after the result looks correct.
