// SPDX-License-Identifier: Apache-2.0
// Previewing never modifies the active stack. Loading/merging is explicit.
export function openPresetGallery({index, selected, previewUrl, onChoose, opener}) {
  const dialog=document.createElement('dialog');dialog.className='fcore-preset-gallery';
  dialog.setAttribute('aria-label','Flare preset gallery');
  const header=document.createElement('header');
  const heading=document.createElement('h2');heading.textContent='Flare presets';
  const close=document.createElement('button');close.textContent='Close';close.className='fcore-btn';
  close.onclick=()=>dialog.close();header.append(heading,close);
  const search=document.createElement('input');search.type='search';search.placeholder='Search presets or categories';
  search.setAttribute('aria-label','Search presets');
  const category=document.createElement('select');category.className='fcore-preset-category';category.setAttribute('aria-label','Preset category');
  for(const value of ['',...new Set(index.map(p=>p.category || 'Other'))].sort((a,b)=>a.localeCompare(b))){
    const option=document.createElement('option');option.value=value;
    option.textContent=value ? `${value} (${index.filter(p=>(p.category || 'Other')===value).length})` : 'All categories';category.append(option);
  }
  const body=document.createElement('div');body.className='fcore-preset-body';
  const grid=document.createElement('div');grid.className='fcore-preset-grid';
  const preview=document.createElement('aside');preview.className='fcore-preset-preview';
  const large=document.createElement('img');large.alt='';
  const title=document.createElement('h3');const info=document.createElement('p');
  const status=document.createElement('p');status.setAttribute('role','status');
  const actions=document.createElement('div');actions.className='fcore-preset-actions';
  const load=document.createElement('button');load.className='fcore-btn accent';load.textContent='Load preset';
  const merge=document.createElement('button');merge.className='fcore-btn';merge.textContent='Merge elements';
  actions.append(load,merge);preview.append(large,title,info,actions,status);body.append(grid,preview);
  dialog.append(header,search,category,body);document.body.append(dialog);
  let active=null,busy=false,pinned=null;
  const show=(item)=>{
    if(busy || active?.name===item.name)return;
    active=item;title.textContent=item.title || item.name;
    info.textContent=[item.category,item.subcategory,'Preview on black · fixed light position'].filter(Boolean).join(' · ');
    status.textContent='';large.alt=`Preview of ${item.title || item.name}`;
    large.src=previewUrl(item.name);
    load.disabled=false;merge.disabled=false;
  };
  large.onerror=()=>{status.textContent='Preview unavailable. You can still try loading this preset.';};
  const choose=async(action)=>{
    if(!active || busy)return;
    busy=true;load.disabled=merge.disabled=true;status.textContent='Loading…';
    try {await onChoose(action,active);dialog.close();}
    catch {status.textContent='Could not load preset. Your current flare is unchanged.';}
    finally {busy=false;load.disabled=merge.disabled=false;}
  };
  load.onclick=()=>choose('load');merge.onclick=()=>choose('add');
  const draw=()=>{
    grid.replaceChildren();
    const query=search.value.toLowerCase();
    const items=index.filter(p=>(!category.value || (p.category || 'Other')===category.value) && [p.title,p.name,p.category,p.subcategory].join(' ').toLowerCase().includes(query));
    for(const item of items){
      const card=document.createElement('button');card.className='fcore-preset-card';card.type='button';
      card.setAttribute('aria-label',`Preview ${item.title || item.name}`);
      const isSelected=selected===item.name || selected===(item.title || item.name);
      card.classList.toggle('selected',isSelected);card.setAttribute('aria-pressed',String(isSelected));
      const image=document.createElement('img');image.loading='lazy';image.alt='';image.src=previewUrl(item.name);
      image.onerror=()=>{image.hidden=true;};
      const label=document.createElement('span');label.textContent=(isSelected?'✓ ':'')+(item.title || item.name);
      const category=document.createElement('small');category.textContent=item.category || 'Other';
      card.append(image,label,category);
      card.classList.toggle('preview-selected',pinned===item.name);
      card.onpointerenter=card.onfocus=()=>{if(!pinned)show(item);};
      card.onclick=()=>{
        if(busy)return;
        pinned=item.name;show(item);
        status.textContent='Preview pinned. Click another thumbnail to change it.';
        for(const other of grid.querySelectorAll('.fcore-preset-card')){
          const chosen=other===card;
          other.classList.toggle('preview-selected',chosen);
          other.setAttribute('aria-pressed',String(chosen));
        }
      }; // click/touch/keyboard selection stays put while browsing other cards
      if(pinned)card.setAttribute('aria-pressed',String(pinned===item.name));
      grid.append(card);
    }
    if(!items.length){const empty=document.createElement('p');empty.textContent='No matching presets.';grid.append(empty);}
  };
  search.oninput=draw;
  category.onchange=draw;
  dialog.addEventListener('close',()=>{dialog.remove();if(opener?.isConnected)opener.focus();},{once:true});
  dialog.addEventListener('click',e=>{if(e.target===dialog){const r=dialog.getBoundingClientRect();if(e.clientX<r.left||e.clientX>r.right||e.clientY<r.top||e.clientY>r.bottom)dialog.close();}});
  draw();load.disabled=merge.disabled=true;
  if(index.length)show(index.find(p=>p.name===selected || p.title===selected)||index[0]);
  else status.textContent='No presets found.';
  dialog.showModal();search.focus();
  return dialog;
}
