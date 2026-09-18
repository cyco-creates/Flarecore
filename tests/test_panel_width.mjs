// Run with: node tests/test_panel_width.mjs
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';

// Exercise the shipped helper without importing the ComfyUI/browser runtime.
const source = readFileSync(new URL('../web/flarecore_ui.js', import.meta.url), 'utf8');
const start = source.indexOf('function fitPanelWidth(');
assert.ok(start >= 0);
const end = source.indexOf('\n}\n', start) + 2;
const fitPanelWidth = vm.runInNewContext(`(${source.slice(start, end)})`);
const node = {size: [1100, 1400], _fcEditorH: 700};
const widgets = [
  [{width: 440}, 460, width => Math.min(width * 9 / 16 + 12, 330)],
  [{width: 440}, 500, () => node._fcEditorH],
  [{width: 380}, 380, () => 260],
];
for (const [widget, fallback, height] of widgets) {
  fitPanelWidth(widget, node, fallback, height);
  for (const width of [1100, 700, 420, 1250]) {
    node.size[0] = width;
    for (const probe of [undefined, 440, 225, 0, NaN]) {
      const size = widget.computeSize(probe);
      assert.equal(size[0], width, 'minimum-size probe must not narrow the panel');
      assert.equal(size[1], height(width));
      widget.width = probe; // simulate a layout integration caching its width
      assert.equal(widget.width, width);
      // Installed ComfyUI DomWidgets.vue uses this formula (margin = 10).
      const overlayWidth = (widget.width ?? node.size[0]) - 2 * 10;
      assert.equal(overlayWidth, width - 20, 'subtract margins exactly once');
      assert.equal(node.size[0], width, 'never resize the node as a side effect');
    }
  }
  node.size[0] = NaN;
  assert.equal(widget.width, fallback);
  node.size[0] = 1100;
}
node._fcEditorH = 850;
assert.equal(widgets[1][0].computeSize()[1], 850, 'height remains live');
for (const name of ['pickerWidget', 'editorWidget', 'widget']) {
  assert.ok(source.includes(`fitPanelWidth(${name}, node,`), `${name} must use the helper`);
}
console.log('Panel width regression passed: three panels, repeated probes, cached widths, resize and live heights.');
