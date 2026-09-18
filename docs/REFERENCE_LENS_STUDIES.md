# Reference lens studies — experimental collection

9 September 2026. **These are working visual studies, not a completed replica library.** The filmed references still show more complex scattering, ghost clipping and focused caustics than these procedural reconstructions. No numerical likeness score or commercial-lens calibration is claimed.

## Use inside ComfyUI

Open the preset dropdown on **Flarecore · Render**, search **Reference studies**, and choose the Spherical or Anamorphic subcategory. Select a thumbnail, then **Load preset**. This activates the editable artistic stack, not the separate Lens Lab ray tracer. The focal length and T-stop in each name identify the reference condition; they are not live optical controls.

Previewing a thumbnail does not change your group. Loading replaces that group's look; Undo restores it. Save the workflow to retain groups, source settings and any edits. Existing `lens_*.json` presets remain unchanged for compatibility. The newer collection uses `study_*.json`; do not confuse the two.

The gallery thumbnail is one fixed source position. Evaluate a study by moving the light from left to centre to right, and try a real shot. Master changes size as well as energy: use the individual intensity controls if you only want to adjust brightness.

## What was studied

Authenticated [Cineflares Lens Lab](https://lenses.cineflares.com/) comparisons: 17 pairs at nine time positions, spanning 15 lens families and 34 displayed focal/stop conditions. There are 153 paired screenshots, including one two-lens product slate; 152 contain flare-test footage. The private manifest records lens headings, selected settings, actual playback time and exclusions. These are displayed references, not raw camera files.

The user's [Real Lens Flares demonstration](https://www.youtube.com/watch?v=PuDztD9oJCM) was studied through its transcript and 58 selected full-resolution frames. Its Canon zoom example is an important warning: an intermediate zoom position produces a strong chromatic cusp that is absent at both ends. Linear scaling between endpoint presets cannot reproduce that behaviour.

The supplied video and reference screenshots are not shipped as textures. All study elements are independently authored procedural shapes. Reference captures, contact sheets and comparisons remain outside the repository.

## Conditions represented by the 15 presets

| Lens test | Condition | Authored anatomy / identity limits |
| --- | --- | --- |
| Kowa Cine Prominar | 25 mm / T4 | Gold front slivers, uneven microghost chain, overlapping rimmed rear discs and a blue outer reflection. T2.3 comparison kept separate; no aperture interpolation claimed. |
| Cooke Anamorphic SF | 32 mm / T2.8 | Blue narrow streak plus flattened reflected lobes at source height; faint upright ghosts below. Not a generic warm oval chain. |
| ARRI Signature Prime | 35 mm / T2.8 | Quiet cool-white source, sparse blue ghosts and one small warm point; no large warm halo. |
| ZEISS Supreme Radiance | 35 mm / T2.8 | Large blue pupil before source, clustered blue opposite pupils and substantial blue veil. Shape clipping increases off-axis. |
| Atlas Orion | 50 mm / T2.8 | Fine long blue line and a separate weaker horizontal reflection below the source. No large amber ghost chain. |
| Panavision C Series | 50 mm / T2.8 | Broad cool scatter, pale lavender line and a compact tilted opposite purple/cyan oval, unlike Orion. |
| Canon K35 | 50 mm / T2.8 | Rose front pupil, a blue/green/violet opposite cluster and clipped spectral far edge. Not uniformly amber. |
| Cooke Speed Panchro | 50 mm / T2.8 | Large gentle neutral/cool source, broad outer arc, almost no large ghost chain. This is not Panchro-i Classic FF. |
| Leitz HUGO | 35 mm / T2.8 | Sparse green cusp inside a very faint blue pupil; small front amber arc, otherwise deep black. |
| ARRI Master Prime | 35 mm / T2.8 | Very restrained white/cool source, nearly invisible green/blue reflections. Quiet is intentional. |
| Atlas Mercury | 54 mm / T2.8 | Fine gold line, large clipped upper amber pupil and quiet lower blue/rose reflections. |
| Hawk V-Lite Vintage 74 | 55 mm / T2.8 | Strong pale double streak, broad blue veil and an opposite rose textured oval. The site does not establish a 1.3x variant. |
| Panavision Primo Classic | 50 mm / T2.8 | Bright front white pupils, opposite cyan/green oval and pink cusp, plus a broad clipped edge reflection. |
| ARRI Master Anamorphic | 50 mm / T2.8 | Quiet source with a weak displaced blue streak; this is the standard lens test, not an added Flare Set. |
| ZEISS Super Speed | 50 mm / T2.8 | Triangular violet caustic inside a soft violet/amber pupil, far sliver and a clipped front white ghost. Standard coated test, not the uncoated variant. |

The full provenance index is [reference_study_sources.json](reference_study_sources.json). Anamorphic independent vertical motion is implemented explicitly: streaks can stay at source height while other reflections cross to the opposite side of the optical centre. Curves are stateless and deterministic; revisiting a source position gives the same output.

## Renderer changes

- **Density bias**, for iris/spectral pupil shapes: negative concentrates the fill toward the centre; positive favours the edge. Zero preserves the old profile.
- **Surface detail**: seeded, filtered density variation for iris/ring/hoop/spectral shapes. It fades below the local pixel footprint. It is procedural variation, not a scan of a lens coating.
- **Ray falloff, fan and ray taper**: independently control longitudinal decay, widening and near-source concentration. Defaults preserve the old ray profile.
- Existing blur, rim layers, clipping, shading, dispersion and source-driven response curves give each study separate source, ghost and veil behaviour.

Hover explanations are available beside the controls. These are appearance parameters, not measurements of glass, coating stacks or diffraction.

## Fitting and verification

The builder in `scripts/build_reference_studies.py` is pure: it prints definitions for review and does not overwrite files. Approved energy fits are recorded in [reference_study_fits.json](reference_study_fits.json); the shipped JSON is checked against the builder.

Energy fitting uses the 6, 8, 12 and 14 second frames where the white source is reliably localizable. Twelve source-absent/clipped/dim frames are excluded. The 10 second frame is withheld from numerical fitting, but is inspected during visual iteration: it is **not an independent blind evaluation**.

The basis is rendered at three times the displayed reference resolution, then area-downsampled. Saturated highlights and the site's Save overlay are excluded from the objective. Authored hues and source finishing are fixed; bounded scalar gains adjust ghost energy. Thin rims have authored lower bounds so an undersampled reference cannot remove them entirely.

Comparisons use a unit white light, estimated source coordinates, fixed linear clipping followed by sRGB display, and no per-image normalization. Unknown exposure, grading, crop and source size prevent photometric calibration. Horizontal source sweeps do not prove behaviour at every source elevation.

Automated tests cover schema validation, reproducible files, stable IDs, hue preservation, nonnegative finite output, portrait and odd-sized frames, reversible motion, independent anamorphic vertical movement, legacy no-op defaults, and new parameter bounds. Software tests establish correctness, **not photographic likeness**.

## Still needed before a replica-quality claim

1. Fit pupil boundaries and internal caustics across more source elevations, not only the mostly horizontal filmed sweeps.
2. Replace overly smooth veiling fields and simplified focused knots with better scattering and caustic models. Several current presets remain visibly more geometric than the footage.
3. Validate focal length and aperture separately. Most wide/normal comparisons change both variables. Fixed-focal aperture comparisons exist for Kowa, Cooke SF, Master Anamorphic and Super Speed, but are not yet implemented as interpolated families.
4. Improve the physical engine only from defensible optical data. A recorded flare does not uniquely reveal a manufacturer's optical prescription or coatings.
5. Obtain an independent visual review on stills, moving sources and real composites. No 8.5/10 sign-off has been established for this collection.

The studies are an evidence-linked editing starting point; they should not replace the footage as the quality target.

