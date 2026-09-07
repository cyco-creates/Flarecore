// SPDX-License-Identifier: Apache-2.0
// Self-contained response editor. Ranges/targets come from Python, not a
// second handwritten table. Exported math is checked against Python fixtures.
export function sampleCurve(points, value, interpolation = 'smooth') {
  if (value <= points[0][0]) return points[0][1];
  for (let i=1; i<points.length; i++) {
    const [a, va] = points[i-1], [b, vb] = points[i];
    if (value <= b) {
      let t = (value-a)/(b-a);
      if (interpolation === 'smooth') t = t*t*(3-2*t);
      return va+(vb-va)*t;
    }
  }
  return points.at(-1)[1];
}

export function validatePoints(points, spec) {
  if (!Array.isArray(points) || points.length<2 || points.length>16)
    throw new Error('Use between 2 and 16 points.');
  points.forEach((p,i)=>{
    if (!Array.isArray(p) || p.length!==2 || !p.every(Number.isFinite))
      throw new Error('Every point needs two finite numbers.');
    if (p[0]<-100 || p[0]>100 || (i && p[0]<=points[i-1][0]))
      throw new Error('Input positions must increase from left to right (-100 to 100).');
    if (p[1]<spec.min || p[1]>spec.max)
      throw new Error(`Response must be between ${spec.min} and ${spec.max}.`);
  });
  return points;
}

export function responseProfile(name, type) {
  const c = (target,driver,points)=>({target,driver,points,interpolation:'smooth'});
  let channels=[];
  if (name==='breathe') channels=[c('opacity','radius',[[0,.35],[.65,.75],[1.4,1.2],[2.4,0]]),c('scale','radius',[[0,.65],[1,1],[2.4,1.55]])];
  if (name==='edge') channels=[c('opacity','edge',[[-.6,0],[-.1,1],[.35,.2],[1,0]]),c('scale','edge',[[-.6,1.7],[0,1.25],[1,.65]])];
  if (name==='ghost') {
    channels=[c('opacity','radius',[[0,.1],[.7,.65],[1.4,1],[2.8,0]]),c('stretch_x','radius',[[0,1],[1.2,.9],[2.8,.45]]),c('scale','radius',[[0,.6],[1,1],[2.8,1.5]])];
    if (['iris','ring','hoop','spectral'].includes(type)) channels.push(c('crescent','radius',[[0,0],[1,.12],[2.8,.8]]));
  }
  return {enabled:true,channels};
}

let schemaPromise;
const loadSchema = () => schemaPromise ||= fetch('/flarecore/motion_schema').then(r=>{
  if (!r.ok) throw new Error('Restart ComfyUI to load the motion controls.');
  return r.json();
}).catch(e=>{schemaPromise=null; throw e;});

const el = (tag, text, parent) => {
  const n=document.createElement(tag);
  if (text!==undefined) n.textContent=text;
  if (parent) parent.append(n);
  return n;
};
const clone = o=>JSON.parse(JSON.stringify(o));

export function createMotionPanel(element, onChange, schemaLoader=loadSchema, uiState={}) {
  const root=el('section'); root.className='fc-motion';
  const style=el('style',undefined,root);
  style.textContent=`
    .fc-motion { grid-column:1/-1; min-width:0; color:#ccc; font:12px/1.4 sans-serif; }
    .fc-motion .fc-motion-toolbar { display:flex; gap:8px; flex-wrap:wrap; align-items:center; margin-bottom:12px; }
    .fc-motion button,.fc-motion select,.fc-motion input[type=number] {
      font:12px/1.4 sans-serif; color:#ddd; background:#1e1e25; border:1px solid #34343e;
      border-radius:4px; padding:5px 8px; max-width:100%; box-sizing:border-box;
      height:32px; min-height:32px; letter-spacing:normal; text-transform:none;
    }
    .fc-motion select { display:block; width:100%; }
    .fc-motion .fc-motion-toolbar > select { width:auto; flex:1 1 180px; }
    .fc-motion button { cursor:pointer; white-space:normal; height:auto; }
    .fc-motion button:disabled { opacity:.45; cursor:default; }
    .fc-motion button:hover:not(:disabled) { border-color:#e8a33d; background:#2a2a33; }
    .fc-motion label { min-width:0; font:12px/1.4 sans-serif; color:#aaa; letter-spacing:normal; text-transform:none; }
    .fc-motion input[type=checkbox] { accent-color:#e8a33d; }
    .fc-motion p { line-height:1.5; margin:6px 0 10px; color:#9696a5; }
    .fc-motion input:focus-visible,.fc-motion button:focus-visible,.fc-motion select:focus-visible { outline:2px solid #e8a33d; outline-offset:2px; }
    .fc-motion-controls { display:grid; grid-template-columns:repeat(auto-fit,minmax(min(180px,100%),1fr)); gap:10px; align-items:end; margin:10px 0; }
    .fc-motion summary { color:#ddd; padding:3px 0; }
    .fc-motion summary:hover { color:#e8a33d; }
  `;
  el('p','Control how this element changes as the light moves. Choose a starting point, or add your own curves.',root);
  const body=el('div','Loading controls…',root);
  // Prevent the graph editor from dragging its parent ComfyUI node.
  for (const event of ['pointerdown','wheel']) root.addEventListener(event,e=>e.stopPropagation());
  root.addEventListener('keydown',e=>{
    // Let the stack editor receive its undo/redo shortcuts.
    if (!(e.ctrlKey || e.metaKey) || !['z','y'].includes(e.key.toLowerCase())) e.stopPropagation();
  });
  schemaLoader().then(schema=>{
    if (!schema.targets || !schema.drivers) throw new Error('Motion contract unavailable. Restart ComfyUI.');
    body.textContent='';
    let motion=clone(element.motion || {enabled:true,channels:[]});
    uiState.open ??= new Set(motion.channels.length ? [motion.channels[0].target] : []);
    const targets=Object.entries(schema.targets).filter(([,v])=>!v.types || v.types.includes(element.type));
    const commit=()=>onChange(clone(motion));
    const tools=el('div',undefined,body); tools.className='fc-motion-toolbar';
    const label=el('label',undefined,tools), enabled=el('input',undefined,label);
    enabled.type='checkbox'; enabled.checked=motion.enabled!==false; enabled.ariaLabel='Enable optical response';
    el('span',' Enable response',label);
    enabled.onchange=()=>{motion.enabled=enabled.checked;commit();};
    const profile=el('select',undefined,tools);profile.ariaLabel='Response starting point';
    [['','Starting point…'],['breathe','Gentle breathing'],['edge','Edge emergence'],['ghost','Ghost squeeze + clipping']].forEach(([v,t])=>{const o=el('option',t,profile);o.value=v;});
    const apply=el('button','Apply starting point',tools);apply.type='button';
    apply.disabled=true;profile.onchange=()=>{apply.disabled=!profile.value;};
    apply.title='Replace this element’s response curves. The stack editor Undo can restore them.';
    apply.onclick=()=>{if(profile.value){motion=responseProfile(profile.value,element.type);commit();}};
    const list=el('div',undefined,body);
    function select(parent,labelText,value,choices,onchange) {
      const lab=el('label',labelText+' ',parent), s=el('select',undefined,lab);
      s.ariaLabel=labelText;
      for (const [v,t] of choices) {const o=el('option',t,s);o.value=v;}
      s.value=value;s.onchange=()=>onchange(s.value);return s;
    }
    motion.channels.forEach((channel,index)=>{
      const box=el('details',undefined,list);box.open=uiState.open.has(channel.target);
      box.ontoggle=()=>{if(box.open)uiState.open.add(channel.target);else uiState.open.delete(channel.target);};
      box.style.cssText='padding:9px 0;border-top:1px solid #34343e';
      el('summary',schema.targets[channel.target]?.label || channel.target,box).style.cursor='pointer';
      const controls=el('div',undefined,box);controls.className='fc-motion-controls';
      select(controls,'Property',channel.target,targets.filter(([k])=>k===channel.target || !motion.channels.some(c=>c.target===k)).map(([k,v])=>[k,v.label]),v=>{
        const spec=schema.targets[v];uiState.open.delete(channel.target);uiState.open.add(v);channel.target=v;
        channel.points=channel.points.map(([x])=>[x,spec.neutral]);commit();
      });
      select(controls,'Driven by',channel.driver,Object.entries(schema.drivers).map(([k,v])=>[k,v.label]),v=>{
        const d=schema.drivers[v];channel.driver=v;
        channel.points=channel.points.map(([,y],i)=>[d.min+(d.max-d.min)*i/(channel.points.length-1),y]);commit();
      });
      select(controls,'Curve',channel.interpolation || 'smooth',[['smooth','Smooth'],['linear','Linear']],v=>{channel.interpolation=v;commit();});
      const remove=el('button','Remove curve',controls);remove.type='button';remove.onclick=()=>{motion.channels.splice(index,1);commit();};
      const spec=schema.targets[channel.target], driver=schema.drivers[channel.driver];
      el('p',driver.hint,box);
      el('p',spec.mode==='multiply' ? 'Response is a multiplier: 1 = unchanged, 0 = off (opacity only).' : spec.mode==='add' ? 'Response is added to the base value. Rotation uses degrees; shift uses half-height units.' : 'Response replaces the base value.',box);
      const canvas=el('canvas',undefined,box);canvas.width=1120;canvas.height=340;
      canvas.style.cssText='width:100%;max-width:720px;height:auto;aspect-ratio:56/17;display:block;background:#101014;border-radius:5px;touch-action:none';
      canvas.ariaLabel='Response curve; drag a point or edit its values below';
      const error=el('div','',box);error.style.color='#ffbd96';error.setAttribute('role','status');
      let selected=0,dragging=false,beforeDrag=null;
      const xmin=Math.min(driver.min,channel.points[0][0]), xmax=Math.max(driver.max,channel.points.at(-1)[0]);
      const ymin=Math.min(0,...channel.points.map(p=>p[1])), ymax=Math.max(1,...channel.points.map(p=>p[1]))*1.15;
      const X=x=>35+(x-xmin)/(xmax-xmin)*505, Y=y=>145-(y-ymin)/(ymax-ymin)*125;
      const ctx=canvas.getContext('2d');
      ctx.scale(2,2);
      const paint=()=>{
        ctx.clearRect(0,0,560,170);ctx.strokeStyle='#2b2b33';ctx.lineWidth=1;
        for(let i=0;i<=4;i++){const y=20+i*31.25;ctx.beginPath();ctx.moveTo(35,y);ctx.lineTo(540,y);ctx.stroke();}
        ctx.fillStyle='#9696a5';ctx.font='11px system-ui';ctx.fillText(xmin.toFixed(2),35,164);ctx.fillText(xmax.toFixed(2),505,164);ctx.fillText(ymin.toFixed(2),1,145);ctx.fillText(ymax.toFixed(2),1,20);
        const first=channel.points[0], last=channel.points.at(-1);
        // Held values are not extra editable points. Distinguish them from
        // the authored curve instead of implying a missing endpoint.
        ctx.strokeStyle='#9696a5';ctx.lineWidth=1;ctx.setLineDash([4,4]);
        ctx.beginPath();ctx.moveTo(X(xmin),Y(first[1]));ctx.lineTo(X(first[0]),Y(first[1]));
        ctx.moveTo(X(last[0]),Y(last[1]));ctx.lineTo(X(xmax),Y(last[1]));ctx.stroke();
        ctx.setLineDash([]);ctx.strokeStyle='#e8a33d';ctx.lineWidth=2;ctx.beginPath();
        for(let i=0;i<=200;i++){const x=first[0]+(last[0]-first[0])*i/200,y=sampleCurve(channel.points,x,channel.interpolation);if(!i)ctx.moveTo(X(x),Y(y));else ctx.lineTo(X(x),Y(y));}ctx.stroke();
        channel.points.forEach(([x,y],i)=>{ctx.beginPath();ctx.arc(X(x),Y(y),i===selected?6:4,0,Math.PI*2);ctx.fillStyle=i===selected?'#ffe0aa':'#e8a33d';ctx.fill();});
        ctx.fillStyle='#ffe0aa';ctx.font='10px sans-serif';
        for (const [p,label] of [[first,'Start'],[last,'End']]) {
          ctx.textAlign=p===last?'right':'left';
          ctx.fillText(label,X(p[0]),Y(p[1])<35?Y(p[1])+18:Y(p[1])-12);
        }
        ctx.textAlign='left';
      };
      const rows=el('div',undefined,box);rows.style.cssText='display:flex;flex-wrap:wrap;gap:8px;margin-top:8px';
      const buildRows=()=>{
        rows.textContent='';
        channel.points.forEach((p,i)=>{
          const row=el('label','Point '+(i+1)+' ',rows);row.style.cssText='display:flex;gap:4px;align-items:center';
          [0,1].forEach(dim=>{
            const input=el('input',undefined,row);input.type='number';input.step='any';input.value=Number(p[dim].toFixed(4));input.style.width='74px';
            input.ariaLabel=`Point ${i+1} ${dim?'response':'input'}`;
            input.onchange=()=>{try{const next=clone(channel.points);if(!input.value.trim())throw new Error('Enter a number.');next[i][dim]=Number(input.value);validatePoints(next,spec);channel.points=next;commit();}catch(e){error.textContent=e.message;}};
          });
        });
      };
      const coords=e=>{const r=canvas.getBoundingClientRect();return [(e.clientX-r.left)*560/r.width,(e.clientY-r.top)*170/r.height];};
      canvas.onpointerdown=e=>{const [x,y]=coords(e);const distances=channel.points.map(p=>Math.hypot(X(p[0])-x,Y(p[1])-y));selected=distances.indexOf(Math.min(...distances));if(distances[selected]>18)return;dragging=true;beforeDrag=clone(channel.points);canvas.setPointerCapture(e.pointerId);paint();};
      canvas.onpointermove=e=>{
        if(!dragging)return;const [px,py]=coords(e);
        let x=xmin+(px-35)/505*(xmax-xmin),y=ymin+(145-py)/125*(ymax-ymin);
        x=Math.max(selected?channel.points[selected-1][0]+.001:xmin,Math.min(selected<channel.points.length-1?channel.points[selected+1][0]-.001:xmax,x));
        // Keep the complete handle inside the plot even when dragging
        // outside the canvas. Numeric edits can still expand the range.
        y=Math.max(spec.min,ymin,Math.min(spec.max,ymax,y));channel.points[selected]=[x,y];paint();
      };
      canvas.onpointerup=e=>{if(dragging){dragging=false;canvas.releasePointerCapture(e.pointerId);try{validatePoints(channel.points,spec);commit();}catch(err){error.textContent=err.message;}}};
      canvas.onpointercancel=()=>{dragging=false;if(beforeDrag)channel.points=beforeDrag;paint();};
      paint();buildRows();
      el('p','Start / End mark the editable endpoints. Dashed tails hold the nearest endpoint value. Use the number fields to extend the visible range.',box);
      const pointButtons=el('div',undefined,box);pointButtons.style.cssText='margin-top:8px;display:flex;flex-wrap:wrap;gap:8px';
      const add=el('button','Add point in largest gap',pointButtons);add.type='button';add.disabled=channel.points.length>=16;
      add.onclick=()=>{let k=1;for(let j=2;j<channel.points.length;j++)if(channel.points[j][0]-channel.points[j-1][0]>channel.points[k][0]-channel.points[k-1][0])k=j;const x=(channel.points[k-1][0]+channel.points[k][0])/2;channel.points.splice(k,0,[x,sampleCurve(channel.points,x,channel.interpolation)]);commit();};
      const del=el('button','Remove last point',pointButtons);del.type='button';del.disabled=channel.points.length<=2;
      del.onclick=()=>{channel.points.pop();commit();};
    });
    const add=el('button','+ Add response curve',body);add.type='button';add.style.marginTop='10px';
    const unused=targets.find(([k])=>!motion.channels.some(c=>c.target===k));add.disabled=!unused;
    add.onclick=()=>{if(!unused)return;const [target,spec]=unused;uiState.open.add(target);motion.channels.push({target,driver:'radius',interpolation:'smooth',points:[[0,spec.neutral],[2,spec.neutral]]});commit();};
    if(!motion.channels.length) el('p','No curves yet. Add a curve or choose a starting point above.',body);
  }).catch(e=>{body.textContent=e.message;});
  return root;
}
