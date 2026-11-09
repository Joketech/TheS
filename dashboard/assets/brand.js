/* Public presentation configuration only. Never store credentials here. */
(function (scope) {
  'use strict';
  const ids = ['mira', 'sage', 'seline', 'surge', 'spectra'];
  const roles = ['Coordination', 'Research', 'Writing', 'Growth', 'Engineering'];
  const defaults = {name:'Mission Control',company:'',attribution:'Portable dashboard',logo_text:'MC',logo_url:'',accent:'#fa754e',accent_soft:'#54b77d',agents:ids.map((id,i)=>({id,label:roles[i],role:roles[i],code:['CO','RE','WR','GR','EN'][i],initials:['CO','RE','WR','GR','EN'][i]}))};
  function text(value, fallback) { return typeof value === 'string' ? value.replace(/[\u0000-\u001f\u007f]/g,'').slice(0,180) : fallback; }
  function normalize(value) {
    const input = value && typeof value === 'object' && !Array.isArray(value) ? value : {};
    const result = {};
    for (const key of ['name','company','attribution','logo_text']) result[key] = text(input[key], defaults[key]);
    if(!result.name.trim()) result.name=defaults.name;
    for (const key of ['accent','accent_soft']) result[key] = typeof input[key] === 'string' && /^#[\da-f]{6}$/i.test(input[key]) ? input[key] : defaults[key];
    result.logo_url = typeof input.logo_url === 'string' && /^\/assets\/(?:[a-zA-Z0-9_-]+\/)*[a-zA-Z0-9_-]+\.(?:svg|png|webp|jpg|jpeg)$/.test(input.logo_url) ? input.logo_url : '';
    result.agents = defaults.agents.map(base => {
      const row = Array.isArray(input.agents) ? input.agents.find(a=>a && a.id===base.id) : null;
      return Object.fromEntries(Object.entries(base).map(([key,v])=>[key,key==='id'?v:text(row?.[key],v)]));
    });
    return result;
  }
  const api = {normalize, current:normalize(null), label(id) {return api.current.agents.find(a=>a.id===String(id).toLowerCase())?.label || String(id);}};
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  scope.MissionBrand = api;
  if (!scope.document) return;
  const doc=scope.document;
  api.apply = function(value) {
    api.current=normalize(value);
    doc.title=api.current.name+(api.current.company?' | '+api.current.company:'');
    doc.documentElement.style.setProperty('--brand-accent',api.current.accent);
    doc.documentElement.style.setProperty('--brand-soft',api.current.accent_soft);
    doc.querySelector('meta[name="theme-color"]')?.setAttribute('content',api.current.accent_soft);
    api.decorate();
    doc.dispatchEvent(new CustomEvent('mission-brand-change',{detail:api.current}));
  };
  api.decorate = function() {
    const brand=api.current;
    for(const e of doc.querySelectorAll('[data-brand]')) {
      const key=e.dataset.brand;
      if(['name','company','attribution','logo_text'].includes(key) && e.textContent!==brand[key]) e.textContent=brand[key];
    }
    for(const mark of doc.querySelectorAll('[data-brand-logo]')) {
      if(mark.dataset.logoUrl===brand.logo_url && mark.dataset.logoText===brand.logo_text) continue;
      mark.dataset.logoUrl=brand.logo_url;
      mark.dataset.logoText=brand.logo_text;
      mark.replaceChildren();
      if(brand.logo_url) {const img=doc.createElement('img');img.src=brand.logo_url;img.alt=brand.name;img.style.cssText='width:100%;height:100%;object-fit:contain';img.onerror=()=>{mark.textContent=brand.logo_text;};mark.append(img);}
      else mark.textContent=brand.logo_text;
    }
    const pattern=new RegExp('\\b('+ids.join('|')+')\\b','gi');
    const walker=doc.createTreeWalker(doc.body,NodeFilter.SHOW_TEXT);
    const nodes=[];
    while(walker.nextNode()) {const n=walker.currentNode;if(!n.parentElement.closest('script,style,textarea,input,option,[data-brand],[data-brand-agent],[data-brand-code],[data-brand-initials],[data-brand-logo]') && pattern.test(n.nodeValue)) nodes.push(n);pattern.lastIndex=0;}
    for(const node of nodes) {
      const fragments=node.nodeValue.split(pattern), fragment=doc.createDocumentFragment();
      fragments.forEach((part,i)=>{if(i%2){const span=doc.createElement('em');span.className='mc-name';span.dataset.brandAgent=part.toLowerCase();span.textContent=api.label(part);fragment.append(span);}else fragment.append(doc.createTextNode(part));});node.replaceWith(fragment);
    }
    for(const e of doc.querySelectorAll('[data-brand-agent]')) {const label=api.label(e.dataset.brandAgent);if(e.textContent!==label)e.textContent=label;}
    for(const e of doc.querySelectorAll('[data-brand-code]')) {const a=brand.agents.find(a=>a.id===e.dataset.brandCode);if(a&&e.textContent!==a.code)e.textContent=a.code;}
    for(const e of doc.querySelectorAll('[data-brand-initials]')) {const a=brand.agents.find(a=>a.id===e.dataset.brandInitials);if(a&&e.textContent!==a.initials)e.textContent=a.initials;}
    for(const e of doc.querySelectorAll('[aria-label],[title]')) for(const attr of ['aria-label','title']) {
      const originalKey=attr==='title'?'brandOriginalTitle':'brandOriginalAria';
      const value=e.getAttribute(attr);if(!value)continue;
      pattern.lastIndex=0;if(!e.dataset[originalKey] && pattern.test(value))e.dataset[originalKey]=value;
      if(e.dataset[originalKey]) {const next=e.dataset[originalKey].replace(pattern,id=>api.label(id));if(next!==value)e.setAttribute(attr,next);}
    }
  };
  function boot() {
    api.apply(api.current);
    let queued=false;
    new MutationObserver(()=>{if(queued)return;queued=true;queueMicrotask(()=>{queued=false;api.decorate();});}).observe(doc.body,{childList:true,subtree:true,characterData:true});
    fetch('/api/brand',{cache:'no-store',credentials:'same-origin',signal:AbortSignal.timeout(5000)}).then(r=>{if(!r.ok)throw Error('Brand configuration unavailable');return r.json();}).then(api.apply).catch(()=>{doc.documentElement.dataset.brandStatus='default';});
  }
  if(doc.readyState==='loading') doc.addEventListener('DOMContentLoaded',boot,{once:true});else boot();
})(typeof window !== 'undefined' ? window : globalThis);
