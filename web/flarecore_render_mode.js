// SPDX-License-Identifier: Apache-2.0
// Presentation follows the active renderer, not a retained artistic recipe.
export function renderMode(preset) {
  const lens=preset?.lens_lab;
  return !lens?.enabled ? 'artistic' : lens.include_artistic ? 'hybrid' : 'physical';
}

export function activeLookLabel(preset) {
  if(renderMode(preset)==='artistic')return preset?.name || preset?.preset_file || 'Choose a preset';
  const lens=preset.lens_lab;
  const coating=lens.coating_strength===0 ? 'Uncoated' : lens.coating_profile==='uniform'
    ? `Uniform ${lens.coating_nm ?? 650} nm` : 'Varied coatings';
  return `Lens Lab · ${coating}${lens.include_artistic ? ' + elements' : ''}`;
}

export function selectedLibraryPreset(preset,index) {
  const key=preset?.preset_file || preset?.name;
  const item=index.find(x=>x.name===key || x.title===key);
  // Old workflows can retain an artistic filename after enabling Lens Lab.
  // Never highlight that recipe as though it describes the active renderer.
  return item && (item.render_mode || 'artistic')===renderMode(preset) ? item.name : null;
}

export function applyLensSettings(preset,settings) {
  const wasPhysical=renderMode(preset)!=='artistic';
  preset.lens_lab=structuredClone(settings);
  if(settings.enabled)preset.name=activeLookLabel(preset);
  else if(wasPhysical)preset.name='Custom artistic stack';
  // Applying a lens configuration makes this an edited look, not the old
  // selected file. Elements and global/source values are deliberately retained.
  delete preset.preset_file;
}
