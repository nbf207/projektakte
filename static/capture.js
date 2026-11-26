const initCapture=()=>{
 const t=window.t||(s=>s);
 const form=document.getElementById('snap-form');if(!form||form.dataset.captureBound)return;
 form.dataset.captureBound='1';
 const camera=document.getElementById('snap-camera'),files=document.getElementById('snap-files'),audio=document.getElementById('voice-file'),status=form.querySelector('.upload-status'),chosen=document.getElementById('snap-files-status');
 let selected=[],uploading=false;
 const resume=new URL(location.href).searchParams.get('capture_resume');
 const key=id=>new URL('/__share-cache__/'+id,location.origin).href;
 async function snapshot(data){
  const copy=new FormData();for(const [name,value] of data)if(!['csrfmiddlewaretoken','capture_token'].includes(name))copy.append(name,value);
  const cache=await caches.open('projektakte-shares-v1');
  await cache.put(key(form.elements.capture_token.value),new Response(copy,{headers:{'X-Share-Created':String(Date.now()),'X-Share-Count':String(files.files.length),'X-Share-Source':'capture'}}));
 }
 const update=()=>{const transfer=new DataTransfer();selected.forEach(file=>transfer.items.add(file));files.files=transfer.files;chosen.textContent=selected.length?selected.map(file=>file.name).join(' · '):'';};
 const add=list=>{for(const file of list){if(!selected.some(old=>old.name===file.name&&old.size===file.size&&old.lastModified===file.lastModified))selected.push(file);}update();};
 camera.addEventListener('change',()=>{add(camera.files);camera.value='';});
 files.addEventListener('change',()=>add([...files.files]));
 audio?.addEventListener('change',()=>{document.getElementById('voice-record-status').textContent=audio.files.length?t('Audio ausgewählt: ')+audio.files[0].name:'';});
 // Clearing all selected files stays a single, explicit action.
 const clear=document.createElement('button');clear.type='button';clear.className='ghost';clear.textContent=t('Dateiauswahl leeren');clear.hidden=true;chosen.after(clear);
 clear.addEventListener('click',()=>{selected=[];update();clear.hidden=true;});
 form.addEventListener('change',()=>{clear.hidden=!selected.length;});
 form.elements.text?.addEventListener('keydown',e=>{if((e.ctrlKey||e.metaKey)&&e.key==='Enter'){e.preventDefault();form.requestSubmit();}});
 if(/^[a-f0-9-]{36}$/.test(resume||'')){
  (async()=>{try{
   const response=await(await caches.open('projektakte-shares-v1')).match(key(resume));
   if(!response)return;
   const data=await response.formData();form.elements.capture_token.value=resume;
   form.elements.text.value=String(data.get('text')||'');
   const project=String(data.get('project')||'');if([...form.elements.project.options].some(option=>option.value===project))form.elements.project.value=project;
   add(data.getAll('files').filter(file=>typeof file!=='string'));
   const recording=data.get('audio');if(audio&&recording&&typeof recording!=='string'&&recording.size){const transfer=new DataTransfer();transfer.items.add(recording);audio.files=transfer.files;}
   if(form.elements.keep_audio)form.elements.keep_audio.checked=data.get('keep_audio')==='1';status.textContent=t('Deine Erfassung wurde nach der Anmeldung wiederhergestellt. Du kannst jetzt speichern.');
  }catch{status.textContent=t('Browserentwurf nicht verfügbar. Bitte Dateien erneut auswählen.');}})();
 }
 form.addEventListener('submit',async event=>{
  event.preventDefault();
  if(uploading||form.dataset.voiceRecording==='1')return;
  const audioFiles=audio?[...audio.files]:[];
  if(!form.elements.text.value.trim()&&!files.files.length&&!audioFiles.length){status.textContent=t('Bitte Text, Aufnahme oder Dateien hinzufügen.');return;}
  const all=[...files.files,...audioFiles];
  if(files.files.length>8||all.some(file=>file.size>8*1024*1024)||all.reduce((sum,file)=>sum+file.size,0)>12*1024*1024){status.textContent=t('Höchstens 8 Fotos/PDFs, je Datei 8 MB und zusammen mit Audio maximal 12 MB verwenden.');return;}
  uploading=true;
  const data=new FormData(form),buttons=[...form.querySelectorAll('button')].map(button=>[button,button.disabled]),progress=form.querySelector('progress');
  buttons.forEach(([button])=>button.disabled=true);progress.hidden=false;status.textContent=t('Wird dauerhaft gespeichert … Danach kannst du das Handy weglegen.');
  const restore=()=>{uploading=false;buttons.forEach(([button,disabled])=>button.disabled=disabled);};
  let saved=false;try{await snapshot(data);saved=true;}catch{}
  progress.removeAttribute('value');
  try{
   const response=await ProjektakteNet.requestJSON(form.action,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'},signal:AbortSignal.timeout(120000)});
   if(response.ok&&response.data?.url){try{await(await caches.open('projektakte-shares-v1')).delete(key(form.elements.capture_token.value));}catch{}location.assign(response.data.url);return;}
   restore();status.textContent=response.data?.error||t('Speicherung nicht bestätigt. Eingaben bleiben erhalten.');
  }catch{
   restore();const target=new URL('/',location.origin);if(saved)target.searchParams.set('capture_resume',form.elements.capture_token.value);
   ProjektakteNet.loginNotice(status,target.href,saved?t('Speicherung nicht bestätigt. Dateien und Notiz bleiben im Browser gespeichert. '):t('Speicherung nicht bestätigt. Vor dem Anmelden bitte Text kopieren; Dateien danach erneut auswählen. '));
  }
 });
};
document.addEventListener('DOMContentLoaded',initCapture);
document.addEventListener('lg:navigate',initCapture);
if(document.readyState!=='loading')initCapture();
