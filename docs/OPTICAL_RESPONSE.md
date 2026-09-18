# Optical response

Optical response curves let an element change as the light moves. They are
useful when a static element feels too uniform across a shot.

## Quick start

1. Open an element in the Flarecore Render editor.
2. Expand **Optical response** and enable the response.
3. Choose a property such as opacity, size, width, height, rotation, softness,
   dispersion or color transmission.
4. Choose what drives it: source position, distance, frame-edge distance,
   brightness or visibility.
5. Drag the curve points or enter their values numerically.
6. Move the light with the picker, a path or a tracker and render.

An opacity or size response of **1** leaves the value unchanged. A response of
**0** turns that property off. Values outside the curve's authored range hold
the nearest endpoint.

## Useful starting points

- **Gentle breathing** adds a small size or opacity change as the source moves.
- **Edge emergence** fades a flare in as the source enters the frame and out as
  it leaves.
- **Ghost squeeze + clipping** changes ghost width and completion near the
  frame edge.

Applying a starting point gives the selected element an editable set of curves.
Use Undo if you want to return to the previous settings.

## Units and behavior

Source X and Y are normalized to the frame: X runs from -1 at the left edge to
1 at the right, and Y from -1 at the top to 1 at the bottom. Radius, edge
distance and screen shifts use half-frame-height units. Rotation responses add
degrees to the element's base rotation.

For an off-frame fade, put a zero-opacity endpoint outside the frame or use the
global edge fade. For horizontal anamorphic streaks, keep auto-rotate off when
you want the streak to remain screen-aligned.

Use separate curves for separate behaviors. For example, let opacity follow
visibility, size follow source distance and dispersion follow brightness. The
curve preview shows the response function; the rendered image shows the final
optical result.
