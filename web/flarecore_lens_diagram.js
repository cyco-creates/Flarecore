// SPDX-License-Identifier: Apache-2.0
// Display geometry only. The renderer's supplied prescription/ray paths stay authoritative.
export function surfaceSag(surface, radius) {
  const r=surface.radius;
  return r ? surface.z+r-Math.sign(r)*Math.sqrt(Math.max(0,r*r-radius*radius)) : surface.z;
}

export function glassSections(surfaces) {
  return surfaces.flatMap((front,i)=>{
    const back=surfaces[i+1];
    if(!back || front.stop || back.stop || front.after<=1) return [];
    // The overlap is the optically usable cutaway, not a manufactured barrel outline.
    const radius=Math.min(front.aperture,back.aperture);
    return [{front,back,radius}];
  });
}

export function drawLensSection(host,data,{selectedSurface=0,selectedPair=null,onSelect=()=>{},focusSelected=false}={}) {
  const ns='http://www.w3.org/2000/svg';
  const el=(tag,attrs={},text)=>{
    const n=document.createElementNS(ns,tag);
    for(const [k,v] of Object.entries(attrs)) n.setAttribute(k,String(v));
    if(text!=null)n.textContent=text;
    return n;
  };
  const model=data.model,svg=el('svg',{role:'group','aria-label':'Interactive lens cutaway'});
  const extent=Math.max(12,...model.surfaces.map(s=>s.aperture));
  const scale=Math.min(660/(model.sensor_z+10),158/(2*extent));
  const x0=(800-(model.sensor_z+10)*scale)/2+5*scale,cy=116;
  svg.setAttribute('viewBox',`${x0-8*scale} 0 ${(model.sensor_z+16)*scale} 248`);
  const xy=(z,r)=>`${(x0+z*scale).toFixed(3)},${(cy-r*scale).toFixed(3)}`;
  const path=(d,attrs={})=>el('path',{d,fill:'none',...attrs});
  const curve=(s,radius,reverse=false)=>Array.from({length:49},(_,i)=>{
    const r=((reverse ? 48-i : i)/48*2-1)*radius;
    return xy(surfaceSag(s,r),r);
  });
  svg.append(el('title',{},'Lens cutaway: incoming light, glass, aperture and image sensor'));
  // A quiet registration grid gives scale without pretending to be a housing design.
  for(let z=0;z<=model.sensor_z;z+=5)svg.append(path(`M${xy(z,-extent-1)}L${xy(z,extent+1)}`,{stroke:'#252932','stroke-width':.65}));
  svg.append(path(`M${xy(-6,0)}L${xy(model.sensor_z+3,0)}`,{stroke:'#697080','stroke-width':.7,'stroke-dasharray':'5 5'}));
  for(const {front,back,radius} of glassSections(model.surfaces)){
    const points=[...curve(front,radius),...curve(back,radius,true)];
    svg.append(path('M'+points.join('L')+'Z',{fill:'#75b9d5','fill-opacity':.16,stroke:'#80bdd0','stroke-opacity':.25,'stroke-width':.7}));
  }
  for(const ray of data.paths || [])if(ray.length>1){
    svg.append(path('M'+ray.map(([z,r])=>xy(z,r)).join('L'),{stroke:selectedPair?'#f4b65e':'#bdd0d8','stroke-width':selectedPair?1.35:.9,opacity:selectedPair ? .8 : .34,'pointer-events':'none'}));
  }
  const pairIds=selectedPair?.split(':').map(Number) || [];
  let selectedButton;
  for(const s of model.surfaces){
    const chosen=selectedSurface===s.id;
    const g=el('g',{class:'surface',tabindex:0,role:'button','aria-pressed':chosen,'aria-label':s.stop?'Select aperture stop':`Select surface ${s.id+1}`});
    if(chosen)selectedButton=g;
    g.append(el('title',{},s.stop?'Aperture · controls the opening':`Surface ${s.id+1} · select to inspect this glass interface`));
    const d=s.stop?`M${xy(s.z,extent)}L${xy(s.z,s.aperture)}M${xy(s.z,-s.aperture)}L${xy(s.z,-extent)}`:'M'+curve(s,s.aperture).join('L');
    g.append(path(d,{stroke:'transparent','stroke-width':12}),path(d,{stroke:chosen?'#91e3ef':pairIds.includes(s.id)?'#ffc06b':s.stop?'#e9ac54':'#7f9fab','stroke-width':chosen?2.8:s.stop?3:1.2}));
    const choose=()=>onSelect(s.id);g.onclick=choose;
    g.onkeydown=e=>{if(e.key==='Enter'||e.key===' '){e.preventDefault();choose();}};
    svg.append(g);
  }
  const sensorX=x0+model.sensor_z*scale;
  svg.append(el('rect',{x:sensorX-3,y:cy-62,width:6,height:124,rx:2,fill:'#a4cfa3'}));
  const text=(x,y,value,fill='#a8aeb9',anchor='middle')=>el('text',{x,y,fill,'font-size':12,'font-family':'system-ui,sans-serif','text-anchor':anchor},value);
  svg.append(text(x0-15,20,'LIGHT IN','#d0d2db'),text(x0+16*scale,20,'GLASS ELEMENTS'),text(sensorX,20,'SENSOR','#a4cfa3'));
  svg.append(path(`M${xy(-6,2)}L${xy(-2,2)}m-5,-3 l5,3 -5,3`,{stroke:'#d0d2db','stroke-width':1.4}));
  const selected=model.surfaces.find(s=>s.id===selectedSurface);
  const stop=model.surfaces.find(s=>s.stop);
  if(stop)svg.append(text(x0+stop.z*scale,213,'APERTURE','#edb365'));
  if(selected && !selected.stop){
    const sx=x0+selected.z*scale;
    svg.append(path(`M${sx},${cy+selected.aperture*scale+4}L${sx},224`,{stroke:'#91e3ef','stroke-width':.75,opacity:.65}));
    svg.append(text(sx,237,`Surface ${selected.id+1}`,'#91e3ef'));
  }
  host.replaceChildren(svg);
  if(focusSelected)selectedButton?.focus();
  return svg;
}
