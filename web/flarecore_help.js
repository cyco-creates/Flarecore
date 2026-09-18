// SPDX-License-Identifier: Apache-2.0
// Shared help affordance for the node and top-layer Lens Lab dialog.
let nextHelp = 0;
export function infoTag(text, label = 'Setting help') {
  if (!document.getElementById('flarecore-help-style')) {
    const style = document.createElement('style'); style.id = 'flarecore-help-style';
    style.textContent = `
      .fc-help{display:inline-flex;align-items:center;justify-content:center;flex:0 0 15px;width:15px;height:15px;margin-left:5px;border:1px solid #777783;border-radius:50%;color:#b8b8c3;font:italic 11px/1 Georgia,serif;cursor:help;vertical-align:middle}
      .fc-help:hover,.fc-help:focus-visible{color:#ffc16c;border-color:#e8a33d;outline:1px solid #e8a33d;outline-offset:2px}
      .fc-help-tip{position:fixed;z-index:10010;box-sizing:border-box;max-width:min(310px,calc(100vw - 16px));padding:10px 12px;color:#ececf2;background:#202027;border:1px solid #555560;border-radius:6px;font:12px/1.5 system-ui,sans-serif;box-shadow:0 5px 20px #0008;white-space:normal;pointer-events:auto}
    `; document.head.append(style);
  }
  const icon = document.createElement('span'); icon.className = 'fc-help'; icon.textContent = 'i';
  icon.tabIndex = 0; icon.setAttribute('role','button'); icon.setAttribute('aria-label',label);
  icon.setAttribute('aria-expanded','false');
  let tip, cleanup, timer;
  const hide = () => { clearTimeout(timer); cleanup?.(); cleanup=null; tip?.remove(); tip=null; icon.removeAttribute('aria-describedby'); icon.setAttribute('aria-expanded','false'); };
  const deferHide = () => { clearTimeout(timer); timer=setTimeout(hide,180); };
  const show = () => {
    clearTimeout(timer); if(tip?.isConnected) return; hide();
    tip=document.createElement('div'); tip.className='fc-help-tip fcore-tip'; tip.id=`fc-help-${++nextHelp}`;
    tip.setAttribute('role','tooltip'); tip.textContent=text;
    // A body child is behind a native dialog, regardless of its z-index.
    (icon.closest('dialog') || document.body).append(tip);
    icon.setAttribute('aria-describedby',tip.id); icon.setAttribute('aria-expanded','true');
    const r=icon.getBoundingClientRect(), box=tip.getBoundingClientRect();
    tip.style.left=`${Math.max(8,Math.min(r.left,window.innerWidth-box.width-8))}px`;
    tip.style.top=`${Math.max(8,r.bottom+box.height+14>window.innerHeight ? r.top-box.height-8 : r.bottom+8)}px`;
    tip.onpointerenter=()=>clearTimeout(timer); tip.onpointerleave=deferHide;
    const abort=new AbortController();
    window.addEventListener('resize',hide,{signal:abort.signal});
    document.addEventListener('scroll',hide,{capture:true,signal:abort.signal});
    const observer=new MutationObserver(()=>{if(!icon.isConnected || !tip?.isConnected) hide();});
    observer.observe(document.body,{childList:true,subtree:true});
    cleanup=()=>{abort.abort();observer.disconnect();};
  };
  icon.onpointerenter=e=>{if(e.pointerType!=='touch')show();};
  icon.onpointerleave=deferHide; icon.onfocus=show; icon.onblur=hide;
  icon.onpointerdown=e=>{e.stopPropagation();e.preventDefault();};
  icon.onclick=e=>{e.stopPropagation();e.preventDefault();tip?.isConnected ? hide():show();};
  icon.onkeydown=e=>{
    if(['Enter',' ','Escape'].includes(e.key)){e.stopPropagation();e.preventDefault();e.key==='Escape' ? hide() : show();}
  };
  return icon;
}
