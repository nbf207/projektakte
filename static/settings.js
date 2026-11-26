document.addEventListener('DOMContentLoaded',()=>{
 const live=document.querySelector('[data-settings-live]');
 if(live){
  const size=n=>n>=1073741824?(n/1073741824).toFixed(2)+' GB':(n/1048576).toFixed(1)+' MB';
  async function update(){
   if(document.visibilityState==='visible')try{
    const response=await ProjektakteNet.requestJSON(live.dataset.settingsLive,{signal:AbortSignal.timeout(5000)});
    if(!response.ok)throw new Error();
    for(const task of response.data.tasks||[]){const row=document.querySelector(`[data-control-task="${task.id}"]`);if(row){row.querySelector('[data-task-state]').textContent=task.state;row.querySelector('[data-task-result]').textContent=task.result;}}
    const values=response.data.monitor;
    if(values)document.querySelectorAll('[data-monitor]').forEach(node=>{const key=node.dataset.monitor,val=values[key];
     if(key==='worker_age')node.textContent=val===null||val===undefined?'Noch unbekannt':'vor '+val+' Sekunden';
     else if(key==='worker_ok')node.textContent=val?'aktiv':'prüfen (langer Auftrag oder Unterbrechung)';
     else if(key==='media')node.textContent=val===null||val===undefined?'noch nicht ermittelt':size(val);
     else if(key==='media_updated')node.textContent=val?' · Stand '+val:'';
     else if(val!==null&&val!==undefined)node.textContent=size(val);
    });
    live.textContent='Status aktualisiert · '+new Date().toLocaleTimeString();
   }catch{live.textContent='Status momentan nicht aktualisierbar. Bei Bedarf Seite neu öffnen / anmelden. Eingaben bleiben erhalten.';}
   setTimeout(update,15000);
  }
  setTimeout(update,15000);
 }
 const button=document.getElementById('copy-feedback');
 if(button)button.addEventListener('click',async()=>{
  const text=document.getElementById('feedback-prompt'),status=document.getElementById('copy-feedback-status');
  try{await navigator.clipboard.writeText(text.value);status.textContent='Offene Punkte kopiert.';}
  catch{text.closest('details').open=true;text.focus();text.select();status.textContent='Bitte den markierten Prompt kopieren.';}
 });
 document.querySelectorAll('form[data-feedback-images]').forEach(form=>{
  const input=form.querySelector('input[type=file][name=image]'),preview=form.querySelector('[data-image-preview]');if(!input)return;
  let previous;
  function show(){if(previous)URL.revokeObjectURL(previous);if(preview)preview.replaceChildren();const file=input.files?.[0];if(!file)return;
   if(file.size>5*1024*1024){input.value='';if(preview)preview.textContent='Bild bitte auf höchstens 5 MB begrenzen.';return;}
   if(preview){previous=URL.createObjectURL(file);const image=document.createElement('img');image.src=previous;image.alt='Ausgewähltes Feedback-Bild';preview.append(image);const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent='Bild entfernen';remove.addEventListener('click',()=>{input.value='';show();const status=form.querySelector('[role=status]');if(status)status.textContent='Bild entfernt. Du kannst ein anderes auswählen oder ohne Bild senden.';});preview.append(remove);}
  }
  input.addEventListener('change',show);
  form.addEventListener('paste',event=>{const image=Array.from(event.clipboardData?.items||[]).find(item=>item.type.startsWith('image/'));if(!image)return;
   const file=image.getAsFile();if(!file)return;event.preventDefault();const files=new DataTransfer();files.items.add(file);input.files=files.files;show();
  });
 });
});
