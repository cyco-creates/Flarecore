import assert from 'node:assert/strict';
let observed;
class Element {
  constructor(tag){this.tagName=tag;this.children=[];this.attributes={};this.style={};this.isConnected=false;}
  append(e){this.children.push(e);e.parent=this;e.isConnected=this.isConnected;}
  remove(){this.isConnected=false;this.parent.children=this.parent.children.filter(e=>e!==this);}
  setAttribute(k,v){this.attributes[k]=v;}
  removeAttribute(k){delete this.attributes[k];}
  getBoundingClientRect(){return this.tagName==='span'?{left:390,top:280,bottom:295}:{width:310,height:80};}
  closest(tag){let p=this.parent;while(p){if(p.tagName===tag)return p;p=p.parent;}return null;}
}
const body=new Element('body');body.isConnected=true;
const head=new Element('head');head.isConnected=true;
globalThis.document={body,head,createElement:t=>new Element(t),getElementById:id=>head.children.find(x=>x.id===id),addEventListener(){}};
globalThis.window={innerWidth:400,innerHeight:300,addEventListener(){}};
globalThis.MutationObserver=class {constructor(fn){this.fn=fn;}observe(){observed=this;}disconnect(){this.closed=true;}};
const {infoTag}=await import('../web/flarecore_help.js');
const event={pointerType:'mouse',stopPropagation(){this.stopped=true;},preventDefault(){this.prevented=true;}};
const icon=infoTag('An explanation','About a setting');body.append(icon);
icon.onpointerenter(event);
let tip=body.children.at(-1);assert.equal(tip.attributes.role,'tooltip');assert.equal(tip.textContent,'An explanation');
assert.equal(tip.style.left,'82px');assert.equal(tip.style.top,'192px');assert.equal(icon.attributes['aria-expanded'],'true');
const escape={...event,key:'Escape'};icon.onkeydown(escape);assert.ok(escape.stopped&&escape.prevented);assert.equal(icon.attributes['aria-expanded'],'false');assert.ok(observed.closed);
icon.onpointerenter({...event,pointerType:'touch'});assert.equal(icon.attributes['aria-expanded'],'false');
icon.onclick(event);assert.equal(icon.attributes['aria-expanded'],'true');icon.onclick(event);assert.equal(icon.attributes['aria-expanded'],'false');
const dialog=new Element('dialog');body.append(dialog);const modalIcon=infoTag('Visible above the dialog');dialog.append(modalIcon);
modalIcon.onfocus();tip=dialog.children.at(-1);assert.equal(tip.attributes.role,'tooltip');assert.equal(head.children.length,1);
modalIcon.onkeydown({...event,key:'Enter'});assert.equal(dialog.children.length,2,'Repeated focus/Enter does not duplicate the tooltip');
modalIcon.isConnected=false;observed.fn();assert.equal(modalIcon.attributes['aria-expanded'],'false');assert.ok(observed.closed);
console.log('Shared help: hover, touch, keyboard, Escape, viewport bounds, native dialog placement and detached cleanup passed.');
