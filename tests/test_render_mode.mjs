import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
import {renderMode,activeLookLabel,selectedLibraryPreset,applyLensSettings} from '../web/flarecore_render_mode.js';
const artistic={name:'Adaptive Warm Glass',preset_file:'adaptive_warm_glass',global:{tint:[1,.8,.6],master:.7},elements:[{id:'old',type:'glow'}]};
const index=[{name:'adaptive_warm_glass',title:'Adaptive Warm Glass',render_mode:'artistic'},
  {name:'lens_lab_reference',title:'PBRT Wide 22 mm',render_mode:'physical'},
  {name:'saved_lens',title:'Saved lens',render_mode:'hybrid'}];
const settings={enabled:true,include_artistic:false,coating_profile:'mixed',coating_nm:650,coating_strength:1,f_stop:4};
const legacy={...structuredClone(artistic),lens_lab:structuredClone(settings)};
assert.equal(renderMode(legacy),'physical');assert.match(activeLookLabel(legacy),/^Lens Lab/);
assert.equal(selectedLibraryPreset(legacy,index),null,'An old artistic filename must not remain selected in physical mode');
assert.equal(selectedLibraryPreset(artistic,index),'adaptive_warm_glass');
const p=structuredClone(artistic),before=structuredClone(p);
applyLensSettings(p,settings);settings.f_stop=16;
assert.equal(p.lens_lab.f_stop,4);assert.equal(p.preset_file,undefined);assert.match(p.name,/^Lens Lab/);
assert.deepEqual(p.elements,before.elements);assert.deepEqual(p.global,before.global);
assert.equal(selectedLibraryPreset(p,index),null);
const restored=JSON.parse(JSON.stringify(p));assert.equal(activeLookLabel(restored),activeLookLabel(p));
p.lens_lab.include_artistic=true;assert.equal(renderMode(p),'hybrid');assert.match(activeLookLabel(p),/\+ elements/);
p.preset_file='saved_lens';assert.equal(selectedLibraryPreset(p,index),'saved_lens');
applyLensSettings(p,{...p.lens_lab,enabled:false});assert.equal(renderMode(p),'artistic');assert.deepEqual(p.elements,before.elements);
assert.equal(p.name,'Custom artistic stack');assert.equal(p.lens_lab.f_stop,4,'Returning to the stack retains optical settings');
assert.match(activeLookLabel({lens_lab:{enabled:true,coating_strength:0}}),/Uncoated/);
assert.match(activeLookLabel({lens_lab:{enabled:true,coating_profile:'uniform',coating_nm:440}}),/440 nm/);
const source=readFileSync(new URL('../web/flarecore_ui.js',import.meta.url),'utf8');
assert.ok(source.includes('selected:selectedLibraryPreset(current,index)'));
assert.ok(source.includes('this.mutate(p => applyLensSettings(p,settings))'));
assert.ok(source.includes("if(!preset.lens_lab.include_artistic){panel.scrollTop=keptScroll;this.syncTriggerPreview();return;}"));
// Exercise the actual panel builder and its handlers, not only source strings.
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attrs={};}
  append(...items){this.children.push(...items);}
  setAttribute(key,value){this.attrs[key]=value;}
}
const start=source.indexOf('  buildPhysicalPanel('),end=source.indexOf('\n  changeGroups(',start);
const panelBuilder=vm.runInNewContext('(function '+source.slice(start,end).trim()+')',{
  document:{createElement:tag=>new Element(tag)},activeLookLabel,applyLensSettings,
  infoTag:()=>new Element('help'),sliderCol:(label,value,spec,change)=>Object.assign(new Element('slider'),{label,value,spec,change})
});
const physical=structuredClone(legacy);
const owner={mutate:fn=>fn(physical),mutateQuiet:fn=>fn(physical)};
let opened=0;
const panel=panelBuilder.call(owner,physical,()=>opened++);
assert.equal(panel.attrs['aria-label'],'Active Lens Lab settings');
assert.match(panel.className,/fcore-list/);
const grid=panel.children.find(x=>x.className==='fcore-source-grid');
assert.equal(grid.children.filter(x=>x.tagName==='slider').length,5);
grid.children.find(x=>x.label==='Ghost exposure').change(7.5);
assert.equal(physical.lens_lab.exposure,7.5);assert.deepEqual(physical.elements,legacy.elements);
const blades=grid.children.flatMap(x=>x.children).find(x=>x.attrs?.['aria-label']==='Aperture blades');
blades.value='9';blades.onchange();assert.equal(physical.lens_lab.blades,9);
const hybridSwitch=panel.children.find(x=>x.className==='fcore-physical-hybrid').children[0];
hybridSwitch.checked=true;hybridSwitch.onchange();assert.equal(renderMode(physical),'hybrid');
const hybridPanel=panelBuilder.call(owner,physical,()=>opened++);
assert.ok(!hybridPanel.children.some(x=>x.className==='fcore-source-grid'),'Hybrid leaves room for active artistic elements');
const actions=hybridPanel.children.find(x=>x.className==='fcore-bar');
actions.children[0].onclick();assert.equal(opened,1);
actions.children[1].onclick();assert.equal(renderMode(physical),'artistic');
assert.equal(physical.lens_lab.exposure,7.5);assert.equal(physical.lens_lab.blades,9);
assert.deepEqual(physical.elements,legacy.elements);
console.log('Active renderer identity, legacy stale selection, apply isolation, serialization, hybrid and retained-stack restoration passed.');
