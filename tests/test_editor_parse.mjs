// SPDX-License-Identifier: Apache-2.0
// Parse the ENTIRE editor, not just the helpers exercised in isolated tests.
import fs from 'node:fs';
import vm from 'node:vm';
for (const name of ['flarecore_ui.js','flarecore_lens_lab.js','flarecore_lens_diagram.js','flarecore_lens_styles.js','flarecore_help.js','flarecore_presets.js','flarecore_render_mode.js']) {
  const source=fs.readFileSync(new URL('../web/'+name,import.meta.url),'utf8')
    .replace(/^import .*?;\s*$/gm,'')
    .replace(/^export (?=(?:async )?function |const )/gm,'');
  new vm.Script(source,{filename:name});
}
console.log('Full editor and Lens Lab syntax parsed successfully.');
