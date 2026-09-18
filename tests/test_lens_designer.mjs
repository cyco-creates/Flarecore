// SPDX-License-Identifier: Apache-2.0
// Exercise the real designer with an offline DOM and controllable preview transport.
import assert from 'node:assert/strict';
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.style={};this.dataset={};this.events={};this._value='';this._text='';this.className='';this.open=false;this.hidden=false;}
  append(...items){for(const item of items){if(item instanceof Element){item.parent=this;this.children.push(item);}else this.children.push(String(item));}}
  replaceChildren(...items){for(const child of this.children)if(child instanceof Element)child.parent=null;this.children=[];this._text='';this.append(...items);}
  set textContent(value){this.replaceChildren();this._text=String(value);}
  get textContent(){return this._text+this.children.map(x=>x.textContent??x).join('');}
  set value(value){this._value=String(value);} get value(){return this._value;}
  get options(){return this.children;} get selectedIndex(){return this.options.findIndex(x=>x.value===this.value);}
  get isConnected(){return this===document.body || this===document.head || !!this.parent?.isConnected;}
  contains(item){return item===this || this.children.some(x=>x instanceof Element&&x.contains(item));}
  setAttribute(k,v){this.attributes[k]=String(v);} removeAttribute(k){delete this.attributes[k];}
  addEventListener(k,fn){(this.events[k]??=[]).push(fn);} dispatch(k,e={}){for(const fn of this.events[k]||[])fn(e);}
  remove(){if(this.parent)this.parent.children=this.parent.children.filter(x=>x!==this);this.parent=null;}
  closest(tag){let p=this;while(p){if(p.tagName===tag)return p;p=p.parent;}return null;}
  focus(){document.activeElement=this;} scrollIntoView(){this.scrolled=true;}
  showModal(){this.open=true;} close(){this.open=false;this.dispatch('close');}
  getBoundingClientRect(){return {left:0,top:0,bottom:288,width:512,height:288};}
  getContext(){return {drawImage:()=>{this.drawCount=(this.drawCount||0)+1;}};}
  setPointerCapture(id){this.pointer=id;} hasPointerCapture(id){return this.pointer===id;} releasePointerCapture(){this.pointer=null;}
  get classList(){return {add:n=>this.classList.toggle(n,true),remove:n=>this.classList.toggle(n,false),toggle:(n,on)=>{const s=new Set(this.className.split(' ').filter(Boolean));(on??!s.has(n))?s.add(n):s.delete(n);this.className=[...s].join(' ');}};}
}
globalThis.document={body:new Element('body'),head:new Element('head'),createElement:t=>new Element(t),createElementNS:(_,t)=>new Element(t),getElementById:id=>all(document.head).find(x=>x.id===id),addEventListener(){}};
globalThis.window={innerWidth:1280,innerHeight:720,addEventListener(){}};
globalThis.ResizeObserver=class {observe(){ }disconnect(){this.closed=true;}};
globalThis.MutationObserver=class {observe(){}disconnect(){}};
globalThis.Image=class {async decode(){}};
const timers=new Map();let serial=0;
globalThis.setTimeout=fn=>{timers.set(++serial,fn);return serial;};globalThis.clearTimeout=id=>timers.delete(id);
function fire(){assert.ok(timers.size,'Expected a scheduled preview');const [id,fn]=timers.entries().next().value;timers.delete(id);return fn();}
function all(root){return [root,...root.children.filter(x=>x instanceof Element).flatMap(all)];}
function label(root,name){const found=all(root).find(x=>x.attributes['aria-label']===name);assert.ok(found,'Missing '+name);return found;}
function button(root,name){const found=all(root).find(x=>x.tagName==='button'&&x.textContent===name);assert.ok(found,'Missing button '+name);return found;}
function input(root,name,value){const field=label(root,name);field.value=value;field.oninput();}
function select(root,name,value){const field=label(root,name);field.value=value;field.onchange();}
const defaults={version:1,model:'test',enabled:true,include_artistic:false,f_stop:4,blades:7,rotation:0,sensor_width:36,sensor_shift:0,exposure:6,quality:'fine',disabled_pairs:[],surfaces:{'0':{reflection:.55}},coating_profile:'mixed',coating_nm:650,coating_strength:1,source_style:'soft',source_glow:.65,source_rays:1,source_size:.008};
const surfaces=[
  {id:0,z:0,radius:20,aperture:7,before:1,after:1.5,stop:false},
  {id:1,z:5,radius:-20,aperture:7,before:1.5,after:1,stop:false},
  {id:2,z:9,radius:0,aperture:3,before:1,after:1,stop:true},
  {id:3,z:12,radius:25,aperture:6,before:1,after:1.6,stop:false},
  {id:4,z:17,radius:-25,aperture:6,before:1.6,after:1,stop:false},
];
const catalog={defaults,models:[{surfaces,sensor_z:40}],coating_offsets:[-200,400,0,100,0],pairs:[{id:'0:1',surfaces:[0,1],label:'S2 ↔ S1'},{id:'3:4',surfaces:[3,4],label:'S5 ↔ S4'}],looks:[
  {id:'balanced',name:'Balanced · varied coatings',settings:{coating_profile:'mixed',coating_nm:650,source_glow:.65,source_rays:1,surfaces:{'0':{reflection:.55}}}},
  {id:'cool',name:'Cool · restrained blue',settings:{coating_profile:'uniform',coating_nm:650,source_glow:.65,source_rays:1,surfaces:{'0':{reflection:.55}}}},
  {id:'warm',name:'Warm · vintage amber',settings:{coating_profile:'uniform',coating_nm:440,source_glow:.7,source_rays:.6,surfaces:{'0':{reflection:.55}}}},
  {id:'bare',name:'Uncoated · optical diagnostic',settings:{coating_strength:0,source_glow:0,source_rays:0,surfaces:{}}},
]};
const {openLensLab,matchLensStudy}=await import('../web/flarecore_lens_lab.js');
const {surfaceSag,glassSections,drawLensSection}=await import('../web/flarecore_lens_diagram.js');
assert.equal(surfaceSag(surfaces[2],3),9);assert.equal(surfaceSag(surfaces[0],0),0);
assert.ok(surfaceSag(surfaces[0],3)>0);assert.ok(surfaceSag(surfaces[1],3)<5);
assert.deepEqual(glassSections(surfaces).map(x=>[x.front.id,x.back.id]),[[0,1],[3,4]],'Only glass, not air gaps or aperture, is filled');
assert.equal(matchLensStudy(defaults,catalog.looks),'balanced');
assert.equal(matchLensStudy({...defaults,source_style:'rays'},catalog.looks),'');
const requests=[];let nextResponse=null,applied=[];
function reply(payload){return {ok:true,status:200,json:async()=>({image:'data:image/png;base64,fixture',seconds:.1,ray_grid:48,note:'Test preview',diagram:{model:catalog.models[0],paths:[[[0,2],[5,1],[40,0]]],pair:payload.pair}})};}
const api={async fetchApi(path,options){if(path==='/flarecore/lens_lab')return {ok:true,json:async()=>structuredClone(catalog)};const payload=JSON.parse(options.body);requests.push({payload,signal:options.signal});return nextResponse?nextResponse(payload):reply(payload);}};
const original=structuredClone({...defaults,disabled_pairs:['3:4'],surfaces:{'0':{reflection:.31,coating_nm:740}},blades:9,quality:'standard'}),saved=structuredClone(original);
const opener=new Element('button');document.body.append(opener);
await openLensLab({api,settings:original,onApply:x=>applied.push(x),opener});
let modal=document.body.children.at(-1),apply=button(modal,'Apply to flare');
assert.equal(modal.dataset.mode,'simple');assert.equal(apply.disabled,true);await fire();assert.equal(apply.disabled,false);
const initialRequests=requests.length;
button(modal,'Advanced').onclick();assert.equal(modal.dataset.mode,'advanced');assert.equal(label(modal,'Blades').value,'9');assert.equal(requests.length,initialRequests);assert.equal(timers.size,0);
button(modal,'Simple').onclick();input(modal,'Light glow',1.2);assert.equal(apply.disabled,true);assert.equal(label(modal,'Source glow').value,'1.2');
button(modal,'Advanced').onclick();input(modal,'Ghost exposure',4.5);assert.equal(label(modal,'Reflection brightness').value,'4.5');
select(modal,'Final ray quality','draft');select(modal,'Blades','11');button(modal,'Simple').onclick();await fire();
assert.equal(requests.at(-1).payload.settings.quality,'draft');assert.equal(requests.at(-1).payload.settings.blades,11);
assert.deepEqual(original,saved,'No live edits leak into caller settings');
// Keyboard tabs share the same draft and roving focus.
button(modal,'Simple').onkeydown({key:'End',preventDefault(){}});assert.equal(document.activeElement,button(modal,'Advanced'));
assert.equal(button(modal,'Advanced').attributes['aria-selected'],'true');
// Surface selection opens the inspector but never schedules a render by itself.
label(modal,'Select surface 4').onclick();assert.equal(label(modal,'Inspect surface').value,'3');assert.equal(timers.size,0);
input(modal,'Reflection remaining',.25);input(modal,'Surface coating',880);await fire();
assert.deepEqual(requests.at(-1).payload.settings.surfaces['3'],{reflection:.25,coating_nm:880});
button(modal,'Use profile coating').onclick();await fire();assert.deepEqual(requests.at(-1).payload.settings.surfaces['3'],{reflection:.25});
select(modal,'Inspect surface','2');assert.match(modal.textContent,/Open Aperture & ghost shape/);assert.equal(timers.size,0);
// Isolated inspection must not turn into destructive path selection on Apply.
button(modal,'Inspect S5 ↔ S4').onclick();await fire();assert.equal(requests.at(-1).payload.pair,'3:4');
assert.deepEqual(requests.at(-1).payload.settings.disabled_pairs,['3:4']);
button(modal,'Simple').onclick();await fire();assert.equal(requests.at(-1).payload.pair,null);assert.equal(requests.at(-1).payload.view,'complete');
assert.deepEqual(requests.at(-1).payload.settings.disabled_pairs,['3:4']);
// Starting looks preserve aperture, final quality, excluded paths and sensor settings.
label(modal,'Choose Warm').onclick();await fire();assert.equal(label(modal,'Light glow').value,'0.7');
assert.equal(label(modal,'Blades').value,'11');assert.equal(label(modal,'Final ray quality').value,'draft');
assert.deepEqual(requests.at(-1).payload.settings.disabled_pairs,['3:4']);
assert.equal(catalog.looks[2].settings.surfaces['0'].reflection,.55);
// In-flight stale replies never enable Apply or paint the previous draft as current.
let release;nextResponse=()=>new Promise(resolve=>{release=resolve;});
input(modal,'Light glow',1.1);const pending=fire();input(modal,'Light glow',1.5);
const canvas=all(modal).find(x=>x.tagName==='canvas'),paintCount=canvas.drawCount;
release(reply(requests.at(-1).payload));await pending;assert.equal(apply.disabled,true);assert.equal(canvas.drawCount,paintCount);
nextResponse=null;await fire();assert.equal(apply.disabled,false);assert.equal(requests.at(-1).payload.settings.source_glow,1.5);
// Output choice is shared across modes and does not waste a preview request.
select(modal,'Render contribution','hybrid');assert.equal(timers.size,0);apply.onclick();
assert.equal(applied.length,1);assert.equal(applied[0].enabled,true);assert.equal(applied[0].include_artistic,true);
assert.equal(applied[0].source_glow,1.5);assert.equal(modal.isConnected,false);assert.equal(requests.at(-1).signal.aborted,true);assert.equal(document.activeElement,opener);
assert.deepEqual(original,saved);assert.equal(timers.size,0);
// Cancel/Escape, contribution off and retry behaviour.
await openLensLab({api,settings:original,onApply:x=>applied.push(x),opener});modal=document.body.children.at(-1);await fire();
select(modal,'Render contribution','artistic');assert.match(modal.textContent,/Lens Lab is off/);
input(modal,'Light rays',3);modal.dispatch('cancel',{preventDefault(){}});assert.equal(applied.length,1);assert.equal(timers.size,0);assert.deepEqual(original,saved);
await openLensLab({api,settings:original,onApply:x=>applied.push(x),opener});modal=document.body.children.at(-1);
nextResponse=()=>({ok:false,status:500,json:async()=>({error:'Test failure'})});await fire();assert.equal(modal.dataset.previewState,'error');assert.equal(button(modal,'Apply to flare').disabled,true);
nextResponse=null;button(modal,'Refine').onclick();await fire();assert.equal(requests.at(-1).payload.refine,true);assert.equal(button(modal,'Apply to flare').disabled,false);button(modal,'Cancel').onclick();
// SVG remains finite, accessible and selectable with keyboard; original geometry stays intact.
const host=new Element('div');let selected;
const before=structuredClone(surfaces),svg=drawLensSection(host,{model:catalog.models[0],paths:[]},{selectedSurface:3,selectedPair:'3:4',onSelect:x=>{selected=x;}});
for(const node of all(svg))for(const value of Object.values(node.attributes))assert.doesNotMatch(value,/NaN|Infinity/);
assert.equal(all(svg).filter(x=>x.attributes.role==='button').length,5);
label(svg,'Select surface 4').onkeydown({key:'Enter',preventDefault(){}});assert.equal(selected,3);
assert.equal(label(svg,'Select surface 4').attributes['aria-pressed'],'true');assert.deepEqual(surfaces,before);
const focused=drawLensSection(host,{model:catalog.models[0],paths:[]},{selectedSurface:4,focusSelected:true});
assert.equal(document.activeElement,label(focused,'Select surface 5'),'Redrawing selected glass preserves keyboard focus');
console.log('Lens designer: shared tabs, transactions, preview races/retry, output modes, looks, path isolation, surface overrides and accessible cutaway passed.');
