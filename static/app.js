document.addEventListener('DOMContentLoaded',()=>{
  const csrf=()=>document.querySelector('input[name=csrfmiddlewaretoken]')?.value;
  // Every draft request must settle, including when the connection disappears.
  const postDraft=(url,data)=>ProjektakteNet.requestJSON(url,{method:'POST',body:data,signal:AbortSignal.timeout(8000)});
  document.body.addEventListener('htmx:configRequest',e=>e.detail.headers['X-CSRFToken']=csrf());
  const autosave=(form,review)=>{
    let timer,latest=null,sending=false,submitting=false;
    let draftVersion=Number(review?form.elements.draft_version.value:form.dataset.draftVersion);
    const status=document.querySelector(review?'#review-save-status':'#save-status');
    const send=async()=>{
      latest=new FormData(form);
      if(sending)return;
      sending=true;
      while(latest&&!submitting){
        const data=latest;latest=null;
        data.set('draft_version',draftVersion);
        if(!review){
          data.set('entry_id',form.dataset.entryId);
          form.querySelectorAll('input[type=checkbox]').forEach(e=>data.set(e.name,e.checked?'1':''));
        }
        const t=window.t||(s=>s);
        status.textContent=review?t('Prüfentwurf wird gespeichert …'):t('Entwurf wird gespeichert …');
        try{
          const response=await postDraft(review?form.dataset.reviewAutosave:form.dataset.autosave,data);
          if(submitting)break;
          const result=response.data;
          if(response.ok){
            draftVersion=review?result.draft_version:result.version;
            if(review)form.elements.draft_version.value=draftVersion;
            else{
              const url=new URL(location);url.searchParams.set('draft',data.get('token'));
              history.replaceState(null,'',url);
            }
            status.textContent=result.message;
          }else status.textContent=result.error||t('Entwurf nicht gespeichert. Eingabe bleibt im Formular.');
        }catch(error){
          if(!submitting){
            status.textContent=t('Entwurf nicht bestätigt. Eingabe bleibt im Formular; Speichern prüft den Versionsstand.');
            if(error.name==='LoginRequired')ProjektakteNet.loginNotice(status,location.href,t('Anmeldung abgelaufen. Vor dem Erneuern bitte noch nicht gespeicherte Texte kopieren. '));
          }
        }
      }
      sending=false;
    };
    form.addEventListener('input',()=>{
      if(submitting)return;
      clearTimeout(timer);status.textContent=(window.t||(s=>s))('Ungespeicherte Änderung …');
      timer=setTimeout(send,650);
    });
    // Native submission is reliable even during autosave. Server-side draft
    // versions are independent, and a late draft cannot recreate confirmed data.
    form.addEventListener('submit',()=>{clearTimeout(timer);submitting=true;latest=null});
  };
  const initContent = () => {
    document.querySelectorAll('form[data-autosave]').forEach(form=>{if(form.dataset.autosaveBound)return;form.dataset.autosaveBound='1';autosave(form,false);});
    document.querySelectorAll('form[data-review-autosave]').forEach(form=>{if(form.dataset.autosaveBound)return;form.dataset.autosaveBound='1';autosave(form,true);});
    const syncAllocationRow=row=>{
      const proj=row.querySelector('[name=alloc_project]');
      const entry=row.querySelector('[name=alloc_entry]');
      const step=row.querySelector('[name=alloc_step]');
      const projId=proj?proj.value:'';
      if(entry){
        [...entry.options].forEach(opt=>{
          if(!opt.dataset.project)return;
          const hide=Boolean(projId&&opt.dataset.project!==projId);
          opt.hidden=hide;opt.disabled=hide;
        });
        const sel=entry.selectedOptions[0];
        if(sel&&sel.disabled)entry.value='';
      }
      const entryId=entry?entry.value:'';
      if(step){
        [...step.options].forEach(opt=>{
          if(!opt.dataset.entry)return;
          const hide=Boolean(!entryId||opt.dataset.entry!==entryId);
          opt.hidden=hide;opt.disabled=hide;
        });
        const sel=step.selectedOptions[0];
        if(sel&&sel.disabled)step.value='';
      }
    };
    const bindAllocationRow=row=>{
      if(row.dataset.allocRowBound)return;
      row.dataset.allocRowBound='1';
      row.querySelector('[name=alloc_project]')?.addEventListener('change',()=>syncAllocationRow(row));
      row.querySelector('[name=alloc_entry]')?.addEventListener('change',()=>syncAllocationRow(row));
      syncAllocationRow(row);
    };
    document.querySelectorAll('.allocation-row').forEach(bindAllocationRow);
    document.querySelectorAll('[data-add-allocation]').forEach(btn=>{if(btn.dataset.allocBound)return;btn.dataset.allocBound='1';btn.addEventListener('click',()=>{const rows=document.querySelector('#allocation-rows'),copy=rows.lastElementChild.cloneNode(true);delete copy.dataset.allocRowBound;copy.querySelectorAll('input').forEach(e=>e.value='');copy.querySelectorAll('select').forEach(e=>e.selectedIndex=0);rows.append(copy);bindAllocationRow(copy);});});
    document.querySelectorAll('form[data-upload]').forEach(form=>{if(form.dataset.uploadBound)return;form.dataset.uploadBound='1';form.addEventListener('submit',event=>{
      const t=window.t||(s=>s);
      event.preventDefault();const xhr=new XMLHttpRequest(),progress=form.querySelector('progress'),status=form.querySelector('.upload-status'),button=form.querySelector('button');button.disabled=true;progress.hidden=false;status.textContent=t('Upload läuft. Annahme wird nach dauerhafter Speicherung bestätigt.');
      xhr.open('POST',form.action||location.href);xhr.setRequestHeader('X-Requested-With','XMLHttpRequest');xhr.upload.onprogress=e=>{if(e.lengthComputable)progress.value=e.loaded/e.total*100};
      xhr.onload=()=>{button.disabled=false;if(xhr.status>=200&&xhr.status<400){location.assign(xhr.responseURL)}else {let message=t('Upload nicht bestätigt. Eingabe bleibt erhalten; Status im Eingang prüfen.');try{message=JSON.parse(xhr.responseText).error||message}catch{}status.textContent=message}};
      xhr.onerror=()=>{button.disabled=false;status.textContent=t('Verbindung unklar. Eingang prüfen; identische Datei wird anhand ihres Hashes wiedererkannt.')};xhr.send(new FormData(form));
    });});
    document.querySelectorAll('[data-ai-field]').forEach(marker=>{if(marker.dataset.aiFieldBound)return;marker.dataset.aiFieldBound='1';const field=document.getElementsByName(marker.dataset.aiField)[0];if(!field)return;const initial=field.value;field.addEventListener('input',()=>{marker.hidden=field.value!==initial});field.addEventListener('change',()=>{marker.hidden=field.value!==initial})});
    document.querySelectorAll('form[data-maintenance-form]').forEach(form=>{
      if(form.dataset.maintenanceBound)return;
      form.dataset.maintenanceBound='1';
      const days=form.elements.interval_days,months=form.elements.interval_months,km=form.elements.interval_km,baseDate=form.elements.base_date,baseMeter=form.elements.base_meter;
      const syncReq=()=>{
        if(baseDate)baseDate.required=Boolean((days&&days.value.trim())||(months&&months.value.trim()));
        if(baseMeter)baseMeter.required=Boolean(km&&km.value.trim());
      };
      [days,months,km].forEach(el=>{if(el){el.addEventListener('input',syncReq);el.addEventListener('change',syncReq);}});
      syncReq();
    });
    if(location.pathname==='/suche/'){const input=document.querySelector('main input[name=q]');if(input&&!input.value)input.focus();}
    restoreChronicle();
  };
  const chronicleKey=()=> 'chronik:'+location.pathname+location.search;
  const chronicleRows=()=>[...document.querySelectorAll('details[data-chronicle-id]')];
  const rememberChronicle=()=>{
    const rows=chronicleRows();if(!rows.length)return;
    try{sessionStorage.setItem(chronicleKey(),JSON.stringify({open:rows.filter(d=>d.open).map(d=>d.dataset.chronicleId),y:scrollY}))}catch{}
  };
  const restoreChronicle=()=>{
    const rows=chronicleRows();if(!rows.length)return;
    try{
      const stored=sessionStorage.getItem(chronicleKey());if(!stored)return;
      const state=JSON.parse(stored);
      rows.forEach(d=>{d.open=(state.open||[]).includes(d.dataset.chronicleId)});
      scrollTo(0,state.y||0);
    }catch{}
  };
  // HTMX replaces row nodes and changes the URL. Resolve both at event time.
  document.addEventListener('toggle',event=>{
    if(event.target.matches?.('details[data-chronicle-id]'))rememberChronicle();
  },true);
  document.addEventListener('htmx:beforeRequest',rememberChronicle);
  document.addEventListener('htmx:afterSettle',restoreChronicle);
  addEventListener('pagehide',rememberChronicle);
  document.addEventListener('lg:navigate',initContent);
  initContent();
});

// Quick-search shortcut ('/' or Ctrl/Cmd+K).
document.addEventListener('DOMContentLoaded',()=>{
  const searchLink=document.querySelector('.window-search');
  document.addEventListener('keydown',event=>{
    const tag=document.activeElement?.tagName;
    const typing=tag==='INPUT'||tag==='TEXTAREA'||tag==='SELECT'||document.activeElement?.isContentEditable;
    const cmdK=(event.ctrlKey||event.metaKey)&&event.key.toLowerCase()==='k';
    const slash=!event.ctrlKey&&!event.metaKey&&!event.altKey&&event.key==='/'&&!typing;
    if(!cmdK&&!slash)return;
    event.preventDefault();
    const localSearch=document.querySelector('main input[name=q]');
    if(localSearch){localSearch.focus();localSearch.select();return;}
    if(searchLink?.href)location.assign(searchLink.href);
  });
});

// Continuous proximity Liquid Glass optical lighting (smooth approach before hover; clean tap gleam on touch).
(() => {
  if (matchMedia('(prefers-reduced-motion:reduce)').matches) return;
  const sel = 'button:not(.ghost),.btn:not(.ghost),.window-search,.sidebar nav a,.tabs a,.project-filter-links a,.project-record';
  const fine = matchMedia('(hover:hover) and (pointer:fine)').matches;
  const REACH = 240;
  let nodes = [], lit = new Set(), raf = 0, px = -9999, py = -9999;
  const scan = () => { nodes = [...document.querySelectorAll(sel)]; };
  const clearAll = () => {
    lit.forEach(el => {
      el.style.setProperty('--lo', '0');
      el.style.setProperty('--lx', '-999px');
      el.style.setProperty('--ly', '-999px');
    });
    lit.clear();
  };
  const paint = () => {
    raf = 0;
    const next = new Set();
    for (let i = 0; i < nodes.length; i++) {
      const el = nodes[i];
      if (!el.isConnected) continue;
      const r = el.getBoundingClientRect();
      if (!r.width || !r.height) continue;
      const dx = px < r.left ? r.left - px : (px > r.right ? px - r.right : 0);
      const dy = py < r.top ? r.top - py : (py > r.bottom ? py - r.bottom : 0);
      const dist = Math.hypot(dx, dy);
      if (dist < REACH) {
        const strength = Math.max(0, Math.min(1, 1 - dist / REACH));
        el.style.setProperty('--lx', Math.round(px - r.left) + 'px');
        el.style.setProperty('--ly', Math.round(py - r.top) + 'px');
        el.style.setProperty('--lo', (strength * strength).toFixed(2));
        next.add(el);
      }
    }
    lit.forEach(el => {
      if (!next.has(el)) {
        el.style.setProperty('--lo', '0');
        el.style.setProperty('--lx', '-999px');
        el.style.setProperty('--ly', '-999px');
      }
    });
    lit = next;
  };
  document.addEventListener('DOMContentLoaded', scan);
  document.addEventListener('htmx:afterSettle', scan);
  document.addEventListener('lg:navigate', scan);
  if (fine) {
    document.addEventListener('pointermove', e => {
      if (e.pointerType !== 'mouse' && e.pointerType !== 'pen') return;
      if (!nodes.length) scan();
      px = e.clientX; py = e.clientY;
      if (!raf) raf = requestAnimationFrame(paint);
    }, {passive: true});
    document.addEventListener('pointerleave', clearAll);
    window.addEventListener('blur', clearAll);
  }
  document.addEventListener('pointerdown', e => {
    if (e.pointerType !== 'touch') return;
    const el = e.target?.closest?.(sel);
    if (!el) return;
    const r = el.getBoundingClientRect();
    el.style.setProperty('--lx', Math.round(e.clientX - r.left) + 'px');
    el.style.setProperty('--ly', Math.round(e.clientY - r.top) + 'px');
    el.style.setProperty('--lo', '0.85');
    setTimeout(() => {
      el.style.setProperty('--lo', '0');
      setTimeout(() => {
        el.style.setProperty('--lx', '-999px');
        el.style.setProperty('--ly', '-999px');
      }, 260);
    }, 180);
  }, {passive: true});
})();

// Smooth iOS 27 cross-page expand-from-origin fallback (native View Transitions handle Chrome/Edge/Safari at 60/120fps).
(() => {
  if (matchMedia('(prefers-reduced-motion:reduce)').matches) return;
  document.addEventListener('click', e => {
    if (e.defaultPrevented || e.button !== 0 || e.metaKey || e.ctrlKey || e.shiftKey || e.altKey) return;
    const link = e.target?.closest?.('a[href]');
    if (!link || link.target || link.hasAttribute('download')) return;
    let url;
    try { url = new URL(link.href, location.href); } catch { return; }
    if (url.origin !== location.origin || url.href === location.href) return;
    const r = link.getBoundingClientRect();
    try {
      sessionStorage.setItem('lg-open-origin', JSON.stringify({
        x: Math.round(r.left + r.width / 2),
        y: Math.round(r.top + r.height / 2),
        t: Date.now()
      }));
    } catch {}
  }, true);
  const applyExpandFallback = () => {
    let raw;
    try {
      raw = sessionStorage.getItem('lg-open-origin');
      sessionStorage.removeItem('lg-open-origin');
    } catch { return; }
    if (!raw || 'onpagereveal' in window) return;
    try {
      const origin = JSON.parse(raw);
      if (!origin || Date.now() - origin.t > 1500) return;
      const main = document.querySelector('.window-layout>main');
      if (!main) return;
      const mr = main.getBoundingClientRect();
      const ox = Math.max(0, Math.min(mr.width, origin.x - mr.left));
      const oy = Math.max(0, Math.min(mr.height, origin.y - mr.top));
      main.style.transformOrigin = ox + 'px ' + oy + 'px';
      main.classList.add('lg-expanding');
      main.addEventListener('animationend', () => {
        main.classList.remove('lg-expanding');
        main.style.removeProperty('transform-origin');
      }, {once: true});
    } catch {}
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', applyExpandFallback);
  else applyExpandFallback();
})();

// Auto-grow textareas smoothly as user types (fallback for browsers without CSS field-sizing: content).
(() => {
  const grow = el => {
    if (!el || el.tagName !== 'TEXTAREA') return;
    el.style.height = 'auto';
    el.style.height = Math.min(el.scrollHeight + 2, Math.round(window.innerHeight * 0.48)) + 'px';
  };
  const initAll = () => document.querySelectorAll('textarea').forEach(el => { if (el.value) grow(el); });
  document.addEventListener('input', e => { if (e.target?.tagName === 'TEXTAREA') grow(e.target); });
  document.addEventListener('DOMContentLoaded', initAll);
  document.addEventListener('lg:navigate', initAll);
})();

// Mobile navigation sheet toggle (on-demand menu in Topbar so quick capture sits above the fold).
(() => {
  const root = document.documentElement;
  const getBtn = () => document.getElementById('mobile-nav-toggle');
  const setOpen = open => {
    root.classList.toggle('nav-open', open);
    const btn = getBtn();
    if (btn) {
      const t = window.t || (s => s);
      btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      btn.setAttribute('aria-label', open ? t('Navigation schließen') : t('Navigation öffnen'));
    }
  };
  document.addEventListener('click', e => {
    const btn = e.target?.closest?.('#mobile-nav-toggle');
    if (btn) {
      setOpen(!root.classList.contains('nav-open'));
      return;
    }
    if (!root.classList.contains('nav-open')) return;
    if (e.target?.closest?.('#app-sidebar a')) { setOpen(false); return; }
    if (!e.target?.closest?.('#app-sidebar')) setOpen(false);
  });
  document.addEventListener('keydown', e => {
    if (e.key === 'Escape' && root.classList.contains('nav-open')) {
      setOpen(false);
      getBtn()?.focus();
    }
  });
  document.addEventListener('lg:navigate', () => setOpen(false));
})();
