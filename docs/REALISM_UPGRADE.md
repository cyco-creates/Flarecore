# Realistic flare looks

Flarecore's library is organized by optical behavior rather than camera-brand
emulation. Start with a preset, then adjust the source, master and element
controls for the shot.

## Preset families

- **Anamorphic** presets emphasize horizontal streaks, uneven bright lobes,
  displaced companions and spectral color.
- **Spherical** presets emphasize rounded ghosts, aperture reflections and
  softer halos.
- **Scenario** presets are starting points for situations such as stage lights,
  headlights, sodium streets, film halation and underwater caustics.

The preset gallery shows categories and a large preview. Click a thumbnail to
pin its preview, then choose **Load preset** or **Merge elements** explicitly.

## Element families

The element library is grouped into:

`glows`, `ghosts`, `rays`, `streaks`, `rings`, `hoops`, `caustics`, and
`lens_dirt`.

Click an element's name in the editor to browse replacements from its family.
Use the Element Forge when you need a custom photographed or generated texture.

## Controls that add optical character

- **Aspect** stretches the flare across the screen, useful for anamorphic
  character.
- **Fringe** adds lateral red/blue separation that grows toward the edges.
- **Dispersion** separates spectral colors along an element's axis.
- **Completion** turns rings, hoops, glints and spectral rings into partial
  arcs.
- **Irregularity** adds seeded variation to ring brightness, iris edges and ray
  lengths. The seed keeps the pattern stable in a video.
- **Shade** lights an element more strongly from one side.
- **Curve** and **dash** shape streaks into bowed or interrupted lines.
- **Lens orbs** create soft, lens-fixed specks whose brightness follows the
  source.
- **Lens plates** add dirt, droplets or grime that reveal according to source
  and scene illumination.

## A restrained starting point

Begin with low intensity and keep ghosts below the source brightness. Use the
flare-only output over black to judge the optical structure before compositing
over the shot. Strong spectral color is best treated as a deliberate coating or
lighting choice, not applied to every element.

For moving footage, use a tracked source, moderate response curves and image or
depth visibility. This keeps the flare's size, energy and motion believable
when the source moves or passes behind an object.
