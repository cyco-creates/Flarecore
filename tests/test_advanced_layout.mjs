// Run with node tests/test_advanced_layout.mjs. Real helper, stale-layout fixture.
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import vm from 'node:vm';
const source=readFileSync(new URL('../web/flarecore_ui.js',import.meta.url),'utf8');
assert.ok(!source.includes('this.node._fcAdvanced)showWidget'), 'group rebuild must never reveal native source controls');
const extract=name=>{const start=source.indexOf(`function ${name}(`);return source.slice(start,source.indexOf('\n}',start)+2);};
const scope={SOURCE_FIELDS:new Set(['light_x']),sceneDocument:n=>n.scene,activeGroup:s=>s?.group,
  PANEL_WIDGETS:new Set(['flare_layout','flare_editor'])};
vm.createContext(scope);
vm.runInContext(['findWidget','nodeWidgets','hideWidget','showWidget','fitEditor','setAdvanced'].map(extract).join('\n'),scope);
function fixture(group=false) {
  const widgets=[...Array.from({length:30},(_,i)=>({name:i===0?'light_x':`w${i}`,type:'number',value:i})),
    {name:'flare_layout',computeSize:()=>[720,330]},
    {name:'flare_editor',y:4000,last_y:9999}]; // stale y must never influence height
  const n={size:[720,1100],widgets,properties:{},scene:{group:group?{}:null},_fcEditorH:400,
    inputs:[{name:'image',link:1},{name:'light_x',widget:{name:'light_x'},link:2}],
    setDirtyCanvas(){},setSize(size){this.size=Array.from(size);scope.fitEditor(this);},
    computeSize(){return [720,74+this.widgets.filter(w=>!w.hidden).reduce((h,w)=>h+(w.computeSize?.()[1]??24)+4,0)];}};
  widgets.at(-1).computeSize=()=>[n.size[0],n._fcEditorH??400];
  scope.setAdvanced(n,false);n.setSize([720,1100]);return n;
}
for(const grouped of [false,true]) {
  const n=fixture(grouped),values=n.widgets.map(w=>w.value),inputs=JSON.stringify(n.inputs);
  const initial=n.size[1],editor=n._fcEditorH;
  for(let i=0;i<20;i++) {
    scope.setAdvanced(n,true);
    assert.equal(n.size[1],initial,'opening is now internal to the panel, not a node resize');
    assert.equal(n._fcEditorH,editor);
    scope.setAdvanced(n,true); // idempotent, including configure callbacks
    scope.setAdvanced(n,false);
    assert.equal(n.size[1],initial,'collapse restores compact height exactly');
    assert.equal(n._fcEditorH,editor);
    assert.ok(n.widgets.slice(0,30).every(w=>w.hidden));
  }
  scope.setAdvanced(n,true);n.setSize([900,n.size[1]+170]);scope.setAdvanced(n,false);
  assert.deepEqual(n.size,[900,initial+170],'manual extra editor space is retained');
  assert.equal(JSON.stringify(n.inputs),inputs,'connections and port order untouched');
  assert.deepEqual(n.widgets.map(w=>w.value),values,'values untouched');
  const saved=fixture(grouped);scope.setAdvanced(saved,true,n.size[1]);
  scope.setAdvanced(saved,false);assert.equal(saved.size[1],n.size[1],'saved expanded height round-trips');
}
console.log('Advanced layout: 40 repeated toggles, idempotence, stale offsets, groups, manual resize, saved height and connections passed.');
