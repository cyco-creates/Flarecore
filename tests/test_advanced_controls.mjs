import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const text=readFileSync(new URL('../web/flarecore_ui.js',import.meta.url),'utf8');
const start=text.indexOf('  advancedPanel() {'),end=text.indexOf('\n  read()',start);
class Element {
  constructor(tag){this.tagName=tag.toUpperCase();this.type='';this.children=[];this.attrs={};this.error='';this.classList={add(){}};}
  append(...x){this.children.push(...x);}
  setAttribute(k,v){this.attrs[k]=v;}
  setCustomValidity(x){this.error=x;}
  reportValidity(){return !this.error;}
  checkValidity(){const n=Number(this.value);return !this.error&&(this.min===undefined||n>=this.min)&&(this.max===undefined||n<=this.max)&&(this.step!=='1'||Number.isInteger(n));}
}
const context={document:{createElement:t=>new Element(t)},infoTag:t=>new Element('span'),TIPS:{},app:{canvas:{}},
  activeGroup:()=>null,sceneDocument:()=>null,SOURCE_FIELDS:new Set(),nodeWidgets:n=>n.widgets,
  setAdvanced:n=>{n.hiddenChecked=true;}};
const panel=vm.runInNewContext('(function '+text.slice(start,end).trim()+')',context);
const definitions={intensity:['FLOAT',{min:0,max:10}],seed:['INT',{min:0,max:100}],blend_mode:[['add','screen']],
  clamp_output:['BOOLEAN',{}],preset_json:['STRING',{multiline:true}],linked:['FLOAT',{min:0,max:1}]};
const widgets=Object.keys(definitions).map(name=>({name,value:name==='preset_json'?'{}':name==='blend_mode'?'add':name==='clamp_output'?true:1,callback(v){this.committed=v;}}));
const editor={node:{widgets,_fcInputDefs:definitions,inputs:[{widget:{name:'linked'},link:12}]},
  widget:widgets.find(w=>w.name==='preset_json'),flushPending(){},snapshot(){this.snapshots=(this.snapshots||0)+1;},build(){this.built=true;}};
const root=panel.call(editor);
const fields=root.children[2].children;
const field=name=>fields.find(r=>r.children.at(-1).attrs['aria-label']===name).children.at(-1);
const intensity=field('intensity');intensity.value='0.8';intensity.oninput();assert.equal(widgets[0].value,.8);assert.equal(widgets[0].committed,.8);
for(const value of ['','NaN','-1','11']){intensity.value=value;intensity.oninput();assert.equal(widgets[0].value,.8);}
const seed=field('seed');seed.value='1.5';seed.oninput();assert.equal(widgets[1].value,1);seed.value='42';seed.onchange();assert.equal(widgets[1].value,42);
const combo=field('blend mode');combo.value='screen';combo.onchange();assert.equal(widgets[2].value,'screen');
const checkbox=field('clamp output');checkbox.checked=false;checkbox.onchange();assert.equal(widgets[3].value,false);
const json=field('preset json');json.value='{';json.onchange();assert.equal(editor.widget.value,'{}');assert.ok(json.error);
json.value='{"name":"edited","elements":[]}';json.onchange();assert.equal(JSON.parse(editor.widget.value).name,'edited');assert.equal(editor.snapshots,1);assert.ok(editor.built);
assert.equal(field('linked').disabled,true);assert.equal(editor.node.inputs[0].link,12);
assert.ok(editor.node.hiddenChecked);
console.log('Advanced control mirrors: decimal/integer validation, empty input, callbacks, combo, boolean, JSON undo and connected read-only passed.');
