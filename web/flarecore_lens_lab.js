// SPDX-License-Identifier: Apache-2.0
// One transactional draft, two presentations. Neither tab measures its Comfy node.
import { infoTag } from './flarecore_help.js';
import { LENS_STYLE } from './flarecore_lens_styles.js';
import { drawLensSection, surfaceSag as sag } from './flarecore_lens_diagram.js';

function element(tag,text,className) {
  const el=document.createElement(tag);
  if(text!=null)el.textContent=text;
  if(className)el.className=className;
  return el;
}
function button(text,fn,className='') {
  const el=element('button',text,className);el.type='button';el.onclick=fn;return el;
}
const HELP = {
  'Prescription': 'The optical geometry used for tracing. This published 22 mm spherical reference is not a measured commercial lens; coating studies change its look, not its glass geometry.',
  'Coating study': 'A starting look for this lens. Replaces coating choices, reflection strengths, ghost exposure and source finishing. Keeps aperture, sensor and your tracked path unchanged. Cancel discards changes.',
  'Preview contribution': 'Inspect the actual traced reflections on their own, or with the separate artistic source glow and rays. This view choice does not change the final render.',
  'Enable traced ghosts': 'Use Lens Lab when this flare is rendered in ComfyUI. When off, the existing artistic stack renders normally. The Lab preview stays on for inspection.',
  'Also render artistic elements': 'Add your existing stack of textures and procedural elements to the Lens Lab result. They stay saved when this is off; nothing is deleted. Avoid duplicating a strong core or bloom in both layers.',
  'Reflection paths': 'Each ghost is light bouncing between two lens surfaces before reaching the sensor. Checkboxes include paths in the final pass; clicking a pair isolates it only for inspection. An isolated path ignores its checkbox and excludes source finishing.',
  'F-number': 'Opens or closes the aperture. Lower values admit more rays and change ghost size and clipping. Higher values stop the lens down. Ghost brightness is not automatically normalized when the aperture changes.',
  'Blades': 'The aperture outline: circular or a polygon with this many sides. Reflections carry the aperture shape through the lens. Source rays are an artistic approximation, not calculated diffraction.',
  'Rotation': 'Turns the aperture polygon and the separate source-ray pattern in degrees. Does not rotate the lens or your tracked source path.',
  'Sensor width': 'Physical sensor width in millimetres. Changes the field of view and where reflected rays land for the same normalized source position. Output height follows the image aspect ratio.',
  'Sensor shift': 'Moves the sensor forward or backward along the optical axis, in millimetres. Changes the focus and spread of ghost footprints. This is not a sideways image offset.',
  'Ghost exposure': 'Brightness of traced reflections only, in stops: +1 doubles them; −1 halves them. Does not brighten the separate source glow/rays. Use Flarecore master brightness to scale the complete look together.',
  'Final ray quality': 'Pupil samples per reflection path in the ComfyUI render: Draft 48², Standard 96², Fine 192². More samples improve difficult boundaries and caustics but take longer. The Lab always previews at 48²; Refine uses 96² at 512 × 288.',
  'Coating profile': 'Varied gives different lens surfaces different coating designs for a richer ghost palette. Uniform gives them the same design. Any individual surface override still takes priority. These are designed coatings, not measured lens data.',
  'Coating design wavelength': 'Quarter-wave design wavelength in nanometres, not physical film thickness or a tint hue. Changes the wavelength-dependent reflected light. With Varied coatings, this is a base value plus each surface’s offset.',
  'Coating strength': 'Blends uncoated Fresnel reflections (0) with the modeled single-layer coating response (1). Changes brightness as well as colour. Cemented glass interfaces remain uncoated.',
  'Source character': 'Style of the separate artistic light-source finishing. Soft emphasizes a luminous halo; Ray-rich emphasizes a star pattern. Neither changes the traced reflections or claims to simulate diffraction.',
  'Source glow': 'Strength of the separate core and continuous halo around the light. Set glow and rays to 0 for a pure traced-ghost render. This is independent of Ghost exposure.',
  'Source rays': 'Strength of the separate artistic rays around the source. Their pattern follows aperture blade count and rotation, stays stable over time, and is independent of Ghost exposure.',
  'Source size': 'Base size of the source finish as a fraction of frame height; 0.01 is 1%. Changes the core, halo and ray reach together. It does not enlarge the physical point source used for ghost tracing.',
  'Inspect surface': 'Select a glass interface to inspect its geometry and adjust its reflection response. The optical-section diagram offers the same selection. Surface selection alone changes no render settings.',
  'Reflection remaining': 'Multiplier on this surface’s reflection response. 1 leaves it unchanged; 0 suppresses reflections here. Multiple ghost paths can change because they share surfaces; transmitted energy is adjusted too.',
  'Surface coating': 'Absolute coating design wavelength for this interface. Moving this slider overrides its profile value. Use profile coating removes the override so global changes affect it again.',
  'Preview source X': 'Horizontal source position for inspection: 0 is left, 0.5 centre, 1 right. Values outside 0–1 test off-screen sources. Does not alter the node’s light position or tracker.',
  'Preview source Y': 'Vertical source position for inspection: 0 is top, 0.5 centre, 1 bottom. Values outside 0–1 test off-screen sources. Does not alter the node’s light position or tracker.',
  'Refine preview': 'Re-render the same 512 × 288 preview with 96² rays per path instead of 48². This does not set final quality. Pixel filtering and output resolution can change very narrow rim peaks in a full-size render.',
};
Object.assign(HELP,{
  'Reflection brightness': HELP['Ghost exposure'],
  'Light glow': HELP['Source glow'],
  'Light rays': HELP['Source rays'],
  'Aperture': HELP['F-number'],
  'Use Lens Lab': HELP['Enable traced ghosts'],
  'Include my artistic elements': HELP['Also render artistic elements'],
  'Starting look': HELP['Coating study'],
});
function caption(title) {
  const span=element('span',title);
  if(HELP[title])span.append(infoTag(HELP[title],`About ${title}`));
  return span;
}
export function surfaceSag(surface,radius) { return sag(surface,radius); }
export function matchLensStudy(draft,looks) {
  return looks.find(look=>draft.source_style==='soft' && Object.entries(look.settings).every(([key,value])=>JSON.stringify(draft[key])===JSON.stringify(value)))?.id || '';
}

export async function openLensLab({api,settings,onApply,opener}) {
  const response=await api.fetchApi('/flarecore/lens_lab');
  if(!response.ok)throw new Error('Lens Lab needs a ComfyUI restart after updating.');
  const catalog=await response.json();
  const draft=structuredClone({...catalog.defaults,...settings});
  let mode='simple',selectedSurface=0,selectedPair=null,currentDiagram=null,previewView='complete';
  let u=.7,v=.3,closed=false,busy=false,revision=0,timer=null,refineNext=false;
  let bindings=[];
  const controller=new AbortController(),modal=element('dialog',null,'fc-lens');
  modal.setAttribute('aria-label','Lens Lab experimental designer');modal.dataset.mode=mode;
  modal.append(element('style',LENS_STYLE));
  const header=element('header'),brand=element('div',null,'brand'),brandText=element('div');
  brandText.append(element('h2','Lens Lab'),element('small','Shape the look. Keep your tracking.'));
  brand.append(element('span','◉','brand-mark'),brandText);
  header.append(brand,element('span','Experimental optics','badge'),button('Close',()=>close()));
  const modebar=element('div',null,'modebar'),tabs=element('div',null,'tabs'),modeDescription=element('span',null,'mode-description');
  tabs.setAttribute('role','tablist');tabs.setAttribute('aria-label','Lens Lab editing mode');
  const tabButtons={};
  for(const name of ['simple','advanced']){
    const tab=button(name==='simple'?'Simple':'Advanced',()=>setMode(name));
    tab.id='fc-lens-tab-'+name;tab.setAttribute('role','tab');tab.setAttribute('aria-controls','fc-lens-panel-'+name);
    tab.onkeydown=e=>{
      if(!['ArrowLeft','ArrowRight','Home','End'].includes(e.key))return;
      e.preventDefault();
      const next=e.key==='Home'?'simple':e.key==='End'?'advanced':mode==='simple'?'advanced':'simple';
      setMode(next);tabButtons[next].focus();
    };
    tabButtons[name]=tab;tabs.append(tab);
  }
  modebar.append(tabs,modeDescription);
  const layout=element('div',null,'layout'),sidebar=element('aside',null,'sidebar');
  const panels={simple:element('div'),advanced:element('div')};
  for(const [name,panel] of Object.entries(panels)){
    panel.id='fc-lens-panel-'+name;panel.setAttribute('role','tabpanel');
    panel.setAttribute('aria-labelledby','fc-lens-tab-'+name);sidebar.append(panel);
  }
  const center=element('main',null,'center');layout.append(sidebar,center);
  const footer=element('footer'),footnote=element('span','Apply changes this flare group. Run in ComfyUI to render.','muted');
  const applyButton=button('Apply to flare',()=>{
    if(applyButton.disabled)return;
    try{onApply(structuredClone(draft));close();}
    catch(err){footnote.textContent=err.message;footnote.classList.add('error');}
  },'primary');
  const outputLabel=element('label',null,'output-select'),outputSelect=element('select');
  outputSelect.setAttribute('aria-label','Render contribution');
  for(const [value,text] of [['physical','Lens only'],['hybrid','Lens + my elements'],['artistic','Artistic stack only']]){const option=element('option',text);option.value=value;outputSelect.append(option);}
  outputLabel.append(element('span','Output'),infoTag('Lens only keeps your artistic elements saved but inactive. Lens + my elements adds them to the lens. Artistic stack only disables the lens without deleting its settings. The inspection preview never includes the artistic stack.','About render contribution'),outputSelect);
  outputSelect.onchange=()=>{draft.enabled=outputSelect.value!=='artistic';if(draft.enabled)draft.include_artistic=outputSelect.value==='hybrid';syncUI();};
  applyButton.disabled=true;footer.append(footnote,outputLabel,button('Cancel',()=>close()),applyButton);
  modal.append(header,modebar,layout,footer);
  function close(){
    if(closed)return;closed=true;clearTimeout(timer);controller.abort();resize.disconnect();
    modal.close();modal.remove();if(opener?.isConnected)opener.focus();
  }
  modal.addEventListener('cancel',e=>{e.preventDefault();close();});
  modal.addEventListener('close',()=>{if(!closed)close();});
  for(const name of ['pointerdown','pointermove','pointerup','wheel','keydown','keyup'])modal.addEventListener(name,e=>e.stopPropagation());

  function range(parent,title,key,min,max,step,suffix='',getter=()=>draft[key],setter=x=>{draft[key]=x;}){
    const label=element('label',null,'field'),top=element('span',null,'field-head'),output=element('output'),input=element('input');
    top.append(caption(title),output);input.type='range';input.setAttribute('aria-label',title);
    Object.assign(input,{min,max,step});
    const sync=()=>{const value=getter();input.value=value;const n=Number(value).toLocaleString(undefined,{maximumFractionDigits:3});output.textContent=suffix===' ƒ'?'f/'+n:n+suffix;};
    sync();bindings.push({input,sync});
    input.oninput=()=>{setter(Number(input.value));schedule();};
    label.append(top,input);parent.append(label);return input;
  }
  function select(parent,title,key,options,onChange=x=>{draft[key]=x;},getter=()=>draft[key],needsPreview=true){
    const label=element('label',null,'field'),input=element('select'),top=element('span',null,'field-head');
    top.append(caption(title));input.setAttribute('aria-label',title);
    for(const [value,text] of options){const o=element('option',text);o.value=value;input.append(o);}
    const sync=()=>{input.value=getter();input.title=input.options[input.selectedIndex]?.textContent || '';};
    sync();bindings.push({input,sync});
    input.onchange=()=>{onChange(input.value);needsPreview?schedule():syncUI();};
    label.append(top,input);parent.append(label);return input;
  }
  const openSections=new Set(['look']);
  const sections={};
  function section(key,title,copy){
    const details=element('details',null,'control-section'),summary=element('summary',title),body=element('div',null,'section-body');
    details.open=openSections.has(key);details.ontoggle=()=>{details.open?openSections.add(key):openSections.delete(key);};
    if(copy)body.append(element('p',copy,'section-copy'));
    details.append(summary,body);panels.advanced.append(details);sections[key]=details;return body;
  }
  const lookButtons=new Map();
  panels.simple.append(element('div','01 / Choose a starting look','eyebrow'));
  const cards=element('div',null,'look-cards');
  const descriptions={balanced:'Varied colour',cool:'Cool reflections',warm:'Warm reflections',bare:'Diagnostic · no glow'};
  for(const look of catalog.looks){
    const card=button('',()=>chooseLook(look.id),'look-card');
    card.dataset.look=look.id;card.setAttribute('aria-label','Choose '+look.name.split(' · ')[0]);card.title=look.name;
    card.append(element('span',null,'palette'),element('strong',look.name.split(' · ')[0]),element('small',descriptions[look.id] || 'Starting look'));
    cards.append(card);lookButtons.set(look.id,card);
  }
  const currentLook=element('p',null,'current-look');panels.simple.append(cards,currentLook);
  panels.simple.append(element('div','02 / Adjust the feel','eyebrow'));
  range(panels.simple,'Reflection brightness','exposure',-10,16,.1,' EV');
  range(panels.simple,'Light glow','source_glow',0,4,.05);
  range(panels.simple,'Light rays','source_rays',0,8,.1);
  range(panels.simple,'Aperture','f_stop',2.8,22,.1,' ƒ');
  const lookBody=section('look','Starting look','Changes coatings and source finish; keeps aperture and tracking.');
  select(lookBody,'Coating study',null,[['','Custom / current settings'],...catalog.looks.map(x=>[x.id,x.name])],id=>chooseLook(id),()=>matchLensStudy(draft,catalog.looks),false);
  const aperture=section('aperture','Aperture & ghost shape','Control the opening that shapes the reflections.');
  range(aperture,'F-number','f_stop',2.8,22,.1,' ƒ');
  select(aperture,'Blades','blades',[[0,'Circular'],...Array.from({length:14},(_,i)=>[i+3,`${i+3} blades`])],x=>{draft.blades=Number(x);});
  range(aperture,'Rotation','rotation',-180,180,1,'°');
  const coating=section('coating','Coatings & colour','A designed spectral response, not a measured commercial lens coating.');
  select(coating,'Coating profile','coating_profile',[['mixed','Varied surface coatings'],['uniform','Uniform coating']]);
  range(coating,'Coating design wavelength','coating_nm',380,1800,5,' nm');
  range(coating,'Coating strength','coating_strength',0,1,.01);
  const finish=section('finish','Light-source finish','Glow and rays are artistic finishing, separate from the traced reflections.');
  select(finish,'Source character','source_style',[['soft','Soft luminous envelope'],['rays','Ray-rich star']]);
  range(finish,'Source glow','source_glow',0,4,.05);range(finish,'Source rays','source_rays',0,8,.1);range(finish,'Source size','source_size',.001,.05,.001);
  const sensor=section('sensor','Sensor, exposure & quality','Geometry controls change where reflections land, not your tracker.');
  range(sensor,'Sensor width','sensor_width',16,70,.1,' mm');range(sensor,'Sensor shift','sensor_shift',-5,5,.05,' mm');
  range(sensor,'Ghost exposure','exposure',-10,16,.1,' EV');
  select(sensor,'Final ray quality','quality',[['draft','Draft · 48 × 48'],['standard','Standard · 96 × 96'],['fine','Fine · 192 × 192']],undefined,undefined,false);
  const surfaceBody=section('surface','Selected glass surface','Click the cutaway or choose a surface here. This is a diagnostic tool, not a separate lens.');
  const surfaceControls=element('div');
  select(surfaceBody,'Inspect surface',null,catalog.models[0].surfaces.map(s=>[s.id,s.stop?'Aperture stop':`Surface ${s.id+1}`]),id=>chooseSurface(Number(id)),()=>selectedSurface,false);
  surfaceBody.append(surfaceControls);
  function buildSurface(){
    surfaceControls.replaceChildren();
    const s=(currentDiagram?.model || catalog.models[0]).surfaces.find(s=>s.id===selectedSurface);
    const facts=element('div',null,'surface-facts');
    for(const [label,value] of [['Position',s.z.toFixed(2)+' mm'],['Radius',s.radius.toFixed(2)+' mm'],['Index in',s.before],['Index out',s.after]]){
      const cell=element('span');cell.append(element('small',label),String(value));facts.append(cell);
    }
    surfaceControls.append(facts);
    if(s.stop){surfaceControls.append(element('p','Open Aperture & ghost shape to adjust this stop.','section-copy'));return;}
    range(surfaceControls,'Reflection remaining',null,0,1,.01,'',()=>draft.surfaces[String(selectedSurface)]?.reflection??1,x=>{
      draft.surfaces[String(selectedSurface)]={...draft.surfaces[String(selectedSurface)],reflection:x};
    });
    const profile=()=>Math.max(380,Math.min(1800,draft.coating_nm+(draft.coating_profile==='mixed'?catalog.coating_offsets?.[selectedSurface] || 0:0)));
    range(surfaceControls,'Surface coating',null,380,1800,5,' nm',()=>draft.surfaces[String(selectedSurface)]?.coating_nm??profile(),x=>{
      draft.surfaces[String(selectedSurface)]={...draft.surfaces[String(selectedSurface)],coating_nm:x};
    });
    const reset=button('Use profile coating',()=>{delete draft.surfaces[String(selectedSurface)]?.coating_nm;schedule();});
    surfaceControls.append(reset);bindings.push({input:reset,sync:()=>{reset.disabled=draft.surfaces[String(selectedSurface)]?.coating_nm==null;}});
  }
  const paths=section('paths','Reflection paths','Check to include in the final render. Inspect a path to see its two bounces; this alone never disables other paths.');
  const pathCount=element('p',null,'section-copy'),allButton=button('Show all enabled paths',()=>{selectedPair=null;schedule();});
  const pathSearch=element('input',null,'search');pathSearch.type='search';pathSearch.placeholder='Find a surface, e.g. S7';pathSearch.setAttribute('aria-label','Search reflection paths');
  const pairList=element('div',null,'pairs');paths.append(pathCount,allButton,pathSearch,pairList);
  const pairRows=[];
  for(const pair of catalog.pairs){
    const row=element('div',null,'pair'),enabled=element('input');enabled.type='checkbox';enabled.setAttribute('aria-label','Render '+pair.label);
    const inspect=button('Inspect '+pair.label,()=>{selectedPair=pair.id;chooseSurface(pair.surfaces[1],false);schedule();});
    enabled.onchange=()=>{draft.disabled_pairs=draft.disabled_pairs.filter(x=>x!==pair.id);if(!enabled.checked)draft.disabled_pairs.push(pair.id);schedule();};
    row.append(enabled,inspect);pairList.append(row);pairRows.push({row,enabled,inspect,pair});
  }
  pathSearch.oninput=()=>{for(const {row,pair} of pairRows)row.hidden=!pair.label.toLowerCase().includes(pathSearch.value.toLowerCase().trim());};
  const previewOptions=section('preview','Preview & reference','Inspection only. Does not move the node’s light or change its tracking.');
  select(previewOptions,'Preview contribution',null,[['complete','Ghosts + source finish'],['ghosts','Traced ghosts only']],x=>{previewView=x;},()=>previewView);
  const coordinates=element('div',null,'coordinates');
  function coordinate(title,value,change){
    const label=element('label'),input=element('input');input.type='number';Object.assign(input,{min:-1,max:2,step:.01,value});
    input.setAttribute('aria-label',title);label.append(caption(title),input);coordinates.append(label);
    input.onchange=()=>{const n=Number(input.value);if(input.value.trim()!=='' && Number.isFinite(n)&&n>=-1&&n<=2){change(n);moveCross();schedule();}else moveCross();};
    return input;
  }
  const sourceU=coordinate('Preview source X',u,x=>{u=x;}),sourceV=coordinate('Preview source Y',v,x=>{v=x;});
  previewOptions.append(coordinates);
  previewOptions.append(element('p','PBRT Wide 22 mm · published spherical reference. Not a measured commercial lens.','section-copy'));
  const scope=section('scope','What this engine simulates');
  scope.append(element('p','Constant-index spherical glass and two-bounce ghosts, with nine-band single-layer coating response. Designed coatings, not measured lens data. No anamorphic optics or zoom. Source glow/rays are separate artistic finishing, not calculated diffraction.','section-copy'));

  const previewHeading=element('div',null,'preview-heading'),viewTitle=element('h3','Lens preview');
  const returnAll=button('Back to full look',()=>{selectedPair=null;previewView='complete';schedule();});returnAll.hidden=true;
  const refine=button('Refine',()=>schedule(true));refine.title=HELP['Refine preview'];
  previewHeading.append(viewTitle,returnAll,refine,infoTag(HELP['Refine preview'],'About Refine preview'));center.append(previewHeading);
  const previewSpace=element('div',null,'previewspace'),viewport=element('div',null,'viewport'),canvas=element('canvas'),cross=element('div',null,'cross');
  canvas.width=512;canvas.height=288;viewport.append(canvas,cross);previewSpace.append(viewport);center.append(previewSpace);
  const status=element('div','Preparing preview…','status');status.setAttribute('role','status');status.setAttribute('aria-live','polite');
  center.append(element('p','Drag the light to explore · preview only, your tracked path stays unchanged.','preview-caption'),status);
  const optics=element('details',null,'optics'),opticsSummary=element('summary');
  opticsSummary.append(element('span','Inside the lens'),infoTag('A meridional cutaway of the actual reference geometry. Filled areas are glass, the amber stop is the aperture, and the green bar is the sensor. Lines come from the renderer: direct rays by default, or an isolated two-bounce reflection in Advanced. Click a surface to inspect it.','About optical section'),element('small','Glass → aperture → sensor'));
  const opticsBody=element('div',null,'optics-body'),diagramHost=element('div',null,'diagram'),diagramFooter=element('div',null,'diagram-footer'),selectedReadout=element('span',null,'selected-readout');
  for(const [name,cls] of [['Glass','glass'],['Direct light',''],['Reflected light','bounce']]){const legend=element('span');legend.append(element('i',null,'legend-dot '+cls),name);diagramFooter.append(legend);}
  diagramFooter.append(selectedReadout);diagramHost.append(element('p','The cutaway appears with your first preview.','diagram-empty'));
  opticsBody.append(diagramHost,diagramFooter,element('p','Click a glass surface to inspect its coating. In Advanced, inspect a reflection path to follow its two bounces.','optics-hint'));optics.append(opticsSummary,opticsBody);center.append(optics);
  const resize=new ResizeObserver(entries=>{
    const r=entries[0].contentRect,w=Math.max(0,Math.min(r.width,r.height*16/9));
    viewport.style.width=w+'px';viewport.style.height=w*9/16+'px';
  });resize.observe(previewSpace);
  function moveCross(){cross.style.left=u*100+'%';cross.style.top=v*100+'%';sourceU.value=u.toFixed(4);sourceV.value=v.toFixed(4);}
  function point(e){const r=viewport.getBoundingClientRect();u=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));v=Math.max(0,Math.min(1,(e.clientY-r.top)/r.height));moveCross();schedule();}
  viewport.onpointerdown=e=>{if(e.button!==0)return;viewport.setPointerCapture(e.pointerId);point(e);};
  viewport.onpointermove=e=>{if(viewport.hasPointerCapture(e.pointerId))point(e);};
  viewport.onpointerup=e=>{if(viewport.hasPointerCapture(e.pointerId))viewport.releasePointerCapture(e.pointerId);};
  function chooseLook(id){
    const look=catalog.looks.find(x=>x.id===id);if(!look)return;
    Object.assign(draft,structuredClone(look.settings),{source_style:'soft'});schedule();
  }
  function chooseSurface(id,reveal=true){
    selectedSurface=id;buildSurface();if(currentDiagram)drawDiagram(currentDiagram);syncUI();
    if(reveal){setMode('advanced');sections.surface.open=true;openSections.add('surface');sections.surface.scrollIntoView({block:'nearest'});}
  }
  function setMode(next){
    const changed=mode!==next;mode=next;modal.dataset.mode=mode;
    for(const name of ['simple','advanced']){const active=name===mode;panels[name].hidden=!active;tabButtons[name].setAttribute('aria-selected',String(active));tabButtons[name].tabIndex=active?0:-1;}
    modeDescription.textContent=mode==='simple'?'Choose a look. Adjust the feel.':'Fine-tune the optics. Inspect what creates each reflection.';
    if(changed){sidebar.scrollTop=0;optics.open=mode==='advanced';}
    // Simple always shows the whole look. Only inspection state changes.
    if(mode==='simple' && (selectedPair || previewView!=='complete')){selectedPair=null;previewView='complete';schedule();}
  }
  function syncUI(){
    bindings=bindings.filter(({input})=>modal.contains(input));
    for(const {sync} of bindings)sync();
    const study=matchLensStudy(draft,catalog.looks);
    outputSelect.value=!draft.enabled?'artistic':draft.include_artistic?'hybrid':'physical';
    currentLook.textContent=study?'Current: '+catalog.looks.find(x=>x.id===study).name:'Custom · your edited settings';
    for(const [id,card] of lookButtons)card.setAttribute('aria-pressed',String(id===study));
    pathCount.textContent=(catalog.pairs.length-draft.disabled_pairs.length)+' of '+catalog.pairs.length+' reflection paths included';
    allButton.classList.toggle('on',selectedPair==null);
    for(const {pair,enabled,inspect} of pairRows){enabled.checked=!draft.disabled_pairs.includes(pair.id);inspect.classList.toggle('on',selectedPair===pair.id);}
    returnAll.hidden=!selectedPair && previewView==='complete';
    viewTitle.textContent=selectedPair?'Isolated reflection · '+catalog.pairs.find(x=>x.id===selectedPair)?.label:previewView==='ghosts'?'Reflections only':'Lens preview · reflections + source finish';
    footnote.textContent=draft.enabled?'Final quality: '+draft.quality+'. Apply, then Run in ComfyUI.':'Lens Lab is off. Apply will restore the artistic stack.';
    if(draft.include_artistic && draft.enabled)footnote.textContent+=' The preview excludes your artistic stack.';
  }
  function drawDiagram(data){
    currentDiagram=data;
    drawLensSection(diagramHost,data,{selectedSurface,selectedPair:data.pair || null,onSelect:chooseSurface,focusSelected:diagramHost.contains(document.activeElement)});
    selectedReadout.textContent=`22.02 mm reference · ${data.model.surfaces.find(s=>s.id===selectedSurface)?.stop?'Aperture': 'Surface '+(selectedSurface+1)} · sensor z ${data.model.sensor_z.toFixed(2)} mm`;
  }
  function schedule(refine=false){
    revision++;refineNext=refine;clearTimeout(timer);syncUI();
    modal.dataset.previewState='pending';optics.classList.add('stale');applyButton.disabled=true;applyButton.title='Wait for the current preview before applying.';
    status.textContent='Updating · previous image is out of date…';
    timer=setTimeout(updatePreview,280);
  }
  async function updatePreview(){
    if(closed || busy)return;busy=true;const sent=revision,refine=refineNext;
    status.classList.remove('error');status.textContent='Tracing the lens…';
    try{
      const r=await api.fetchApi('/flarecore/lens_lab/preview',{method:'POST',headers:{'Content-Type':'application/json'},signal:controller.signal,body:JSON.stringify({settings:draft,u,v,pair:selectedPair,refine,view:previewView})});
      if(r.status===429){timer=setTimeout(updatePreview,1000);return;}
      const result=await r.json();if(!r.ok)throw new Error(result.error || 'Lens preview failed');
      if(closed || sent!==revision)return;
      const img=new Image();img.src=result.image;await img.decode();
      if(closed || sent!==revision)return;
      canvas.getContext('2d').drawImage(img,0,0);drawDiagram(result.diagram);
      status.textContent=`Ready · ${result.seconds.toFixed(1)} s · ${result.ray_grid}² preview rays · white light on black`;
      status.title=result.note+' · Final ray quality is set separately.';
      modal.dataset.previewState='ready';optics.classList.remove('stale');applyButton.disabled=false;applyButton.title='Apply these lens settings to the selected flare group.';
    }catch(err){
      if(!closed && sent===revision){status.textContent=err.message+' · Use Refine to retry.';status.classList.add('error');modal.dataset.previewState='error';}
    }finally{busy=false;if(!closed && sent!==revision){clearTimeout(timer);timer=setTimeout(updatePreview,100);}}
  }
  buildSurface();setMode('simple');moveCross();document.body.append(modal);modal.showModal();schedule();
  return {close};
}
