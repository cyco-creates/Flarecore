# Commercial lens-inspired flare studies

Fifteen independent, editable artistic presets, named after the commercial lens families that informed their design. **These are not measured replicas or 15 new physical lens prescriptions.** Brand names identify the reference only; no endorsement or affiliation is implied.

The presets use Flarecore's existing artistic element engine. Lens Lab remains a separate experimental tracer based on the published PBRT Wide 22 mm prescription. These studies do not simulate a lens's image formation, focus, bokeh, resolution, distortion, sensor response or proprietary coatings. Ghost spacing, sizes, shapes, colors and intensity are authored interpretations. No commercial test image is included in the preset assets.

## Find and use them

Open the preset-name button in Flarecore Render (not the element-library button). Search **Lens inspired**, or choose Spherical / Anamorphic and the matching subcategory. The three shelves are Modern, Character and Anamorphic. Every preset title ends in **inspired**.

Previewing does not change your flare. **Load preset** replaces the selected group's look; **Merge elements** adds the artistic elements to its existing stack. Save a workflow to keep your scene and tracking. Save a preset to keep the chosen group's look.

All fifteen are self-contained procedural stacks: no additional texture downloads, image generation, API key or paid credits. They intentionally differ in flare strength. Modern high-contrast lenses should not all produce the strong ghosts of an enhanced-flare lens.

For an already photographed practical, disable the four source-finish elements (source core, source inner light, source envelope, fine source rays) if its own core and bloom are sufficient. Start gently with additive linear-light compositing and adjust node intensity to the plate. Match source radius to the whole luminous emitter for image-based obstruction. Anamorphic ovals remain aligned to the image, while streaks taper away from the source.

## Research and interpretation

Sources checked September 8, 2026. The notes distinguish manufacturer descriptions from our design choices; they are not calibration claims.

| Reference family | Shelf | Basis and limits |
| --- | --- | --- |
| ARRI Signature Prime | Modern | ARRI describes soft, delicate flares. This restrained warm core and sparse ghost layout is an authored interpretation. [Manufacturer reference](https://www.arri.com/en/company/press/press-releases-2020/the-full-range-of-arri-signature-prime-lenses-are-now-available-and-shipping) |
| ARRI ZEISS Master Prime | Modern | The manufacturer emphasizes reduced flare and high contrast. This deliberately quiet preset is not designed to resemble a flare-heavy vintage lens. [Manufacturer reference](https://www.arri.com/en/camera-systems/cine-lenses/master-prime-lenses/master-prime-lenses) |
| ZEISS Supreme Prime Radiance | Modern | ZEISS describes controlled blue flares from T* blue coatings. Ghost sizes, spacing and response here are artistic, not a recovered prescription. [Manufacturer reference](https://lenspire.zeiss.com/cine/en/article/from-ghostbuster-to-ghostfather) |
| Cooke S8-i FF | Modern | Cooke emphasizes optimized contrast and an organic, film-inspired image. The warm neutral flare palette is a design choice; no measured S8/i flare hue is claimed. [Manufacturer reference](https://cookeoptics.com/lens/s8-i-ff/) |
| Panavision Primo | Modern | Panavision describes negligible veiling glare and ghosting. This is a subtle, high-contrast interpretation, not a colorful ghost-chain showcase. [Manufacturer reference](https://www.panavision.com/camera-and-optics/optics/product-detail/sl-primo) |
| Leitz HUGO | Character | Leitz describes lower contrast and artful flares with Leica M heritage. The amber/rose palette and arc layout are authored choices. [Manufacturer reference](https://www.leitz-cine.com/product/hugo) |
| Cooke Panchro-i Classic FF | Character | A modern recreation of the classic look, with flare control emphasized by Cooke. Warmer softness here is deliberately more restrained than the enhanced-flare studies. [Manufacturer reference](https://cookeoptics.com/lens/cooke-panchro-classic-i-ff/) |
| Canon Sumire Prime | Character | Canon describes a nuanced wide-aperture look, warm colors and an eleven-blade iris. The gentle round ghosts are authored; this preset does not simulate focus falloff or skin rendering. [Manufacturer reference](https://www.usa.canon.com/content/dam/canon-assets/brochures/pro/Broadcast-and-Cinema-Lens-Catalog-2021.pdf) |
| Panavision SPF Flare Lenses | Character | Panavision describes modified coatings/internal features for increased flare and lower contrast. This reproduces only a flare-layer interpretation, not the lenses' resolution or full-image response. [Manufacturer reference](https://www.panavision.com/camera-and-optics/optics/product-detail/spf-flare-lenses) |
| Panavision C Series | Anamorphic | Panavision describes pronounced anamorphic flare and organic character. Blue streaks and asymmetric ovals here are one interpretation, not a universal color for every C Series lens. [Manufacturer reference](https://www.panavision.com/camera-and-optics/optics/product-detail/c-c-series) |
| Cooke Anamorphic-i SF | Anamorphic | Cooke's SF variants enhance flare; the manufacturer discusses streaks and elliptical orbs. This pale warm streak/oval mixture is an artistic study, not a specified S35 or full-frame prescription. [Manufacturer reference](https://cookeoptics.com/news-and-events/an-introduction-to-anamorphic-lenses/) |
| ARRI Master Anamorphic Flare Set | Anamorphic | ARRI offers front/rear flare sets changing flare, ghosting and veiling glare. This represents an enhanced-flare interpretation, not a measured front/rear combination. [Manufacturer reference](https://www.arri.com/en/cine-lenses/arri-zeiss-fujinon-lenses/master-anamorphics) |
| Atlas Orion | Anamorphic | Atlas describes controlled signature flare and vintage character. The blue treatment is an authored visual interpretation, not measured spectral data. [Manufacturer reference](https://atlaslensco.com/products/orion-series-anamorphic-lenses) |
| Atlas Mercury | Anamorphic | Atlas explicitly describes sun-tinged golden flares for Mercury. Gold streaks are the reference; the 1.5x lens squeeze is not used as a false physical simulation parameter. [Manufacturer reference](https://atlaslensco.com/products/mercury-series-anamorphic-lenses?variant=47926393504002) |
| Hawk V-Lite Vintage 74 1.3x | Anamorphic | Vantage describes reduced contrast, enhanced flare and a 1970s feel for this 1.3x variant. The mixed palette and ghost geometry are artistic, not measured Hawk optics. [Manufacturer reference](https://www.vantagefilm.com/en/products/hawk-enhanced-flares/hawk-1-3x) |

The machine-readable index, filenames and source notes are in [lens_inspired_sources.json](lens_inspired_sources.json). No specific focal length, aperture or coating variant is claimed unless it is part of the reference family's name. Even then, the preset is not a recovered optical design.

## Verification

The dedicated tests validate all 15 presets without warnings, check names and category membership, unique element identities, distinct geometry, and finite deterministic rendering on odd-sized portrait frames. The opt-in render script produces 1080p black-background looks and photographic composites with fixed exposure, raw linear passes, exact preset data and file fingerprints. These checks establish correct operation and inspectable results, not measured similarity to commercial lenses.

