(()=>{
 const t=window.t||(s=>s);
 const size=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':(n/1048576).toFixed(1)+' MB';
 const phaseLabels={
  paperless:'Paperless-Belege werden gesammelt …',
  snapshot:'Datenbank- und Medien-Snapshot wird erstellt …',
  archive:'Sicherungsarchiv wird gepackt …',
  verify:'Sicherungsarchiv wird geprüft …',
  finalize:'Sicherung wird abgeschlossen …',
  done:'Sicherung fertiggestellt.',
  aborted:'Vorherige Sicherung wurde unterbrochen.',
  error:'Sicherung fehlgeschlagen.'
 };
 let pollTimer=null;
 let pollToken=0;

 function stopPolling(){
  if(pollTimer!==null){clearTimeout(pollTimer);pollTimer=null;}
  pollToken+=1;
 }

 function renderBackupProgress(box,bp,backupsCount){
  if(!box)return false;
  const phaseEl=box.querySelector('[data-bp-phase]');
  const pctEl=box.querySelector('[data-bp-percent]');
  const bar=box.querySelector('[data-bp-bar]');
  const detailEl=box.querySelector('[data-bp-detail]');
  if(!bp)return false;
  const running=bp.status==='running';
  if(running){
   box.hidden=false;
   box.dataset.wasRunning='1';
   const pct=Math.max(0,Math.min(100,Number(bp.percent)||0));
   if(phaseEl)phaseEl.textContent=t(phaseLabels[bp.phase]||'Sicherung läuft …');
   if(pctEl)pctEl.textContent=pct+' %';
   if(bar)bar.value=pct;
   if(detailEl){
    const parts=[];
    if(bp.bytes_total>0)parts.push(`${size(bp.bytes_done||0)} / ${size(bp.bytes_total)}`);
    if(bp.files_total>0)parts.push(t('{done} / {total} Dateien',{done:bp.files_done||0,total:bp.files_total}));
    detailEl.textContent=parts.length?parts.join(' · '):t('Die App bleibt während der Sicherung normal nutzbar.');
   }
   return true;
  }
  if(box.dataset.wasRunning==='1'){
   box.dataset.wasRunning='';
   box.hidden=false;
   if(phaseEl)phaseEl.textContent=t(phaseLabels[bp.status]||phaseLabels[bp.phase]||'Sicherung abgeschlossen.');
   if(pctEl)pctEl.textContent=(bp.status==='done'?100:0)+' %';
   if(bar)bar.value=bp.status==='done'?100:0;
   if(detailEl){
    if(bp.status==='done'){
     detailEl.textContent=t('Sicherung abgeschlossen. Seite neu laden, um den neuen Download anzuzeigen.');
     const prevCount=Number(box.dataset.backupsCount||'-1');
     if(typeof backupsCount==='number'&&backupsCount!==prevCount){
      box.dataset.backupsCount=String(backupsCount);
     }
    }else{
     detailEl.textContent=t('Bitte unter „Letzte Hintergrund-Aktionen“ oder Fehlermeldungen prüfen.');
    }
   }
  }
  return false;
 }

 function initSettings(){
  stopPolling();
  const live=document.querySelector('[data-settings-live]');
  if(live){
   const myToken=pollToken;
   const bpBox=document.querySelector('[data-backup-progress]');
   const hasRunningInitially=Boolean(bpBox&&!bpBox.hidden);

   async function update(){
    if(myToken!==pollToken||!document.body.contains(live))return;
    let fast=false;
    if(document.visibilityState==='visible')try{
     const response=await ProjektakteNet.requestJSON(live.dataset.settingsLive,{signal:AbortSignal.timeout(5000)});
     if(myToken!==pollToken||!document.body.contains(live))return;
     if(!response.ok)throw new Error();
     for(const task of response.data.tasks||[]){
      const row=document.querySelector(`[data-control-task="${task.id}"]`);
      if(row){row.querySelector('[data-task-state]').textContent=task.state;row.querySelector('[data-task-result]').textContent=task.result;}
     }
     const values=response.data.monitor;
     if(values)document.querySelectorAll('[data-monitor]').forEach(node=>{const key=node.dataset.monitor,val=values[key];
      if(key==='worker_age')node.textContent=val===null||val===undefined?t('Noch unbekannt'):t('vor {sec} Sekunden',{sec:val});
      else if(key==='worker_ok')node.textContent=val?t('aktiv'):t('prüfen (langer Auftrag oder Unterbrechung)');
      else if(key==='media')node.textContent=val===null||val===undefined?t('noch nicht ermittelt'):size(val);
      else if(key==='media_updated')node.textContent=val?t(' · Stand ')+val:'';
      else if(val!==null&&val!==undefined)node.textContent=size(val);
     });
     fast=renderBackupProgress(bpBox,response.data.backup_progress,response.data.backups_count);
     live.textContent=t('Status aktualisiert · ')+new Date().toLocaleTimeString();
    }catch{
     if(myToken!==pollToken||!document.body.contains(live))return;
     live.textContent=t('Status momentan nicht aktualisierbar. Bei Bedarf Seite neu öffnen / anmelden. Eingaben bleiben erhalten.');
    }
    if(myToken===pollToken&&document.body.contains(live)){
     pollTimer=setTimeout(update,fast?2500:15000);
    }
   }
   pollTimer=setTimeout(update,hasRunningInitially?1500:5000);
  }

  const button=document.getElementById('copy-feedback');
  if(button&&!button.dataset.bound){
   button.dataset.bound='1';
   button.addEventListener('click',async()=>{
    const text=document.getElementById('feedback-prompt'),status=document.getElementById('copy-feedback-status');
    try{await navigator.clipboard.writeText(text.value);status.textContent=t('Offene Punkte kopiert.');}
    catch{text.closest('details').open=true;text.focus();text.select();status.textContent=t('Bitte den markierten Prompt kopieren.');}
   });
  }

  document.querySelectorAll('form[data-feedback-images]').forEach(form=>{
   if(form.dataset.bound)return;
   form.dataset.bound='1';
   const input=form.querySelector('input[type=file][name=image]'),preview=form.querySelector('[data-image-preview]');if(!input)return;
   let previous;
   function show(){if(previous)URL.revokeObjectURL(previous);if(preview)preview.replaceChildren();const file=input.files?.[0];if(!file)return;
    if(file.size>5*1024*1024){input.value='';if(preview)preview.textContent=t('Bild bitte auf höchstens 5 MB begrenzen.');return;}
    if(preview){previous=URL.createObjectURL(file);const image=document.createElement('img');image.src=previous;image.alt=t('Ausgewähltes Feedback-Bild');preview.append(image);const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent=t('Bild entfernen');remove.addEventListener('click',()=>{input.value='';show();const status=form.querySelector('[role=status]');if(status)status.textContent=t('Bild entfernt. Du kannst ein anderes auswählen oder ohne Bild senden.');});preview.append(remove);}
   }
   input.addEventListener('change',show);
   form.addEventListener('paste',event=>{const image=Array.from(event.clipboardData?.items||[]).find(item=>item.type.startsWith('image/'));if(!image)return;
    const file=image.getAsFile();if(!file)return;event.preventDefault();const files=new DataTransfer();files.items.add(file);input.files=files.files;show();
   });
  });
 }

 document.addEventListener('DOMContentLoaded',initSettings);
 document.addEventListener('lg:navigate',initSettings);
})();
