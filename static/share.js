document.addEventListener('DOMContentLoaded',async()=>{
 const form=document.getElementById('share-form');if(!form)return;
 const CACHE='projektakte-shares-v1',PREFIX='/__share-cache__/',TTL=24*60*60*1000;
 const input=document.getElementById('share-files'),preview=document.getElementById('share-preview'),status=document.getElementById('share-status'),message=form.querySelector('.upload-status'),submit=form.querySelector('[data-capture-submit]'),audio=document.getElementById('voice-file');
 const params=new URLSearchParams(location.search),rawId=params.get('paket'),batchId=/^[a-f0-9-]{36}$/.test(rawId||'')?rawId:null,key=batchId?new URL(PREFIX+batchId,location.origin).href:null;
 if(params.has('auth_refresh')){const clean=new URL(location.href);clean.searchParams.delete('auth_refresh');history.replaceState(null,'',clean);}
 let selected=[],urls=[],unsupported=new Set(),uploading=false,hydrating=true,timer,cacheWrites=Promise.resolve(),created=Date.now(),conversionCount=0;
 submit.disabled=true;
 const errors={dateien:'Bitte 1–8 Dateien, je höchstens 8 MB und zusammen 12 MB teilen.',format:'Geteiltes Dateiformat nicht unterstützt. Bitte Bilder oder PDFs teilen.',voll:'Der lokale Teilen-Eingang enthält bereits fünf Pakete. Bitte vorhandene Pakete zuerst speichern.',speicher:'Browser-Zwischenspeicherung fehlgeschlagen. Dateien erneut teilen oder unten auswählen.'};
 const cache=()=>caches.open(CACHE);
 const revoke=()=>{urls.forEach(url=>URL.revokeObjectURL(url));urls=[];};
 const sync=()=>{
  const transfer=new DataTransfer();selected.forEach(file=>transfer.items.add(file));input.files=transfer.files;
  revoke();preview.replaceChildren();
  selected.forEach((file,index)=>{
   const figure=document.createElement('figure'),url=URL.createObjectURL(file);urls.push(url);
   if(file.type.startsWith('image/')){const image=document.createElement('img');image.src=url;image.alt=file.name;figure.append(image);}
   else{const label=document.createElement('div');label.className='share-pdf';label.textContent='PDF';figure.append(label);}
   const link=document.createElement('a');link.href=url;link.target='_blank';link.rel='noopener';link.textContent=file.name+' · '+Math.ceil(file.size/1024)+' KB';figure.append(link);
   const remove=document.createElement('button');remove.type='button';remove.className='ghost';remove.textContent='Entfernen';remove.addEventListener('click',()=>{unsupported.delete(file);selected.splice(index,1);sync();scheduleDraft();});figure.append(remove);preview.append(figure);
  });
  submit.disabled=hydrating||unsupported.size>0||uploading||form.dataset.voiceRecording==='1';
 };
 async function compatible(file){
  if(file.type==='application/pdf'||/\.pdf$/i.test(file.name))return file;
  const byName={jpg:'image/jpeg',jpeg:'image/jpeg',png:'image/png',webp:'image/webp'};
  const type=file.type||byName[file.name.split('.').pop().toLowerCase()];
  if(['image/jpeg','image/png','image/webp'].includes(type))return file.type?file:new File([file],file.name,{type,lastModified:file.lastModified});
  // Browser conversion only; no imaging/audio models or extra Pi dependencies.
  const bitmap=await createImageBitmap(file);
  try{
   const scale=Math.min(1,2048/Math.max(bitmap.width,bitmap.height)),canvas=document.createElement('canvas');canvas.width=Math.max(1,Math.round(bitmap.width*scale));canvas.height=Math.max(1,Math.round(bitmap.height*scale));canvas.getContext('2d').drawImage(bitmap,0,0,canvas.width,canvas.height);
   const blob=await new Promise(resolve=>canvas.toBlob(resolve,'image/jpeg',.92));if(!blob)throw Error('conversion');conversionCount++;
   return new File([blob],file.name+'.jpg',{type:'image/jpeg',lastModified:file.lastModified});
  }finally{bitmap.close();}
 }
 async function addFiles(files){
  for(const original of files){
   if(selected.some(file=>file.name===original.name&&file.size===original.size&&file.lastModified===original.lastModified))continue;
   let file=original;try{file=await compatible(original);}catch{unsupported.add(file);}
   selected.push(file);
  }
  sync();if(unsupported.size)message.textContent='Ein Bildformat konnte im Browser nicht umgewandelt werden. Bitte das betreffende Bild entfernen und als JPEG, PNG oder WebP teilen. Das lokale Paket bleibt erhalten.';
 }
 function saveDraft(){
  if(!key||hydrating)return cacheWrites;
  const draft=new FormData(form);draft.delete('csrfmiddlewaretoken');draft.delete('capture_token');
  cacheWrites=cacheWrites.catch(()=>{}).then(async()=>{const store=await cache();await store.put(key,new Response(draft,{headers:{'X-Share-Created':String(created),'X-Share-Count':String(selected.length)}}));});
  return cacheWrites;
 }
 function scheduleDraft(){if(uploading||hydrating)return;clearTimeout(timer);timer=setTimeout(()=>saveDraft().catch(()=>{}),500);}
 input.addEventListener('change',async()=>{submit.disabled=true;await addFiles([...input.files]);scheduleDraft();});
 form.addEventListener('input',scheduleDraft);form.addEventListener('change',scheduleDraft);
 try{
  if(key){
   const stored=await (await cache()).match(key);
   if(!stored||Date.now()-Number(stored.headers.get('X-Share-Created'))>TTL){status.textContent='Das geteilte Paket fehlt oder ist abgelaufen. Bitte erneut aus der Galerie teilen oder Dateien auswählen.';}
   else{
    created=Number(stored.headers.get('X-Share-Created'));const data=await stored.formData();
    form.elements.capture_token.value=batchId;
    form.elements.text.value=form.elements.text.value||String(data.get('text')||'');
    const project=String(data.get('project')||'');if(!form.elements.project.value&&[...form.elements.project.options].some(option=>option.value===project))form.elements.project.value=project;
    const savedAudio=data.get('audio');if(savedAudio&&typeof savedAudio!=='string'&&savedAudio.size&&!audio.files.length){const transfer=new DataTransfer();transfer.items.add(savedAudio);audio.files=transfer.files;}
    form.elements.keep_audio.checked=data.get('keep_audio')==='1';
    await addFiles(data.getAll('files').filter(file=>typeof file!=='string'));
    status.textContent=selected.length+' Datei(en) übernommen. Projekt auswählen und bestätigen.'+(conversionCount?' '+conversionCount+' Bild(er) für die Verarbeitung im Browser in JPEG umgewandelt.':'');
   }
  }else status.textContent=errors[params.get('fehler')]||'Teile Bilder/PDFs aus einer Android-App mit Projektakte oder wähle sie hier aus.';
 }catch{status.textContent='Lokales Paket konnte nicht gelesen werden. Bitte erneut teilen oder Dateien auswählen.';}
 hydrating=false;sync();
 // Recovery links survive expired sessions and separate simultaneous shares.
 try{
  const pending=document.getElementById('share-pending'),store=await cache();
  for(const request of await store.keys()){
   const stored=await store.match(request),at=Number(stored?.headers.get('X-Share-Created'));
   if(!at||Date.now()-at>TTL){await store.delete(request);continue;}
   const id=new URL(request.url).pathname.slice(PREFIX.length);if(id===batchId||!/^[a-f0-9-]{36}$/.test(id))continue;
   const p=document.createElement('p'),link=document.createElement('a');link.href=stored.headers.get('X-Share-Source')==='capture'?'/?capture_resume='+id:'/teilen/?paket='+id;link.textContent='Noch nicht gesendet: '+stored.headers.get('X-Share-Count')+' Datei(en) · '+new Date(at).toLocaleString('de-DE');p.append(link);pending.append(p);
  }
 }catch{}
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(uploading||hydrating||form.dataset.voiceRecording==='1')return;
  if(unsupported.size){message.textContent='Bitte nicht lesbare Bildformate entfernen oder als JPEG, PNG bzw. WebP erneut auswählen.';return;}
  const files=[...input.files,...audio.files];
  if(input.files.length<1||input.files.length>8||files.some(file=>file.size>8*1024*1024)||files.reduce((sum,file)=>sum+file.size,0)>12*1024*1024){message.textContent='1–8 Bilder/PDFs, je Datei 8 MB und zusammen mit Audio höchstens 12 MB verwenden.';return;}
  uploading=true;clearTimeout(timer);try{await saveDraft();}catch{}
  const data=new FormData(form),buttons=[...form.querySelectorAll('button')].map(button=>[button,button.disabled]),progress=form.querySelector('progress');
  buttons.forEach(([button])=>button.disabled=true);progress.hidden=false;message.textContent='Dateien werden dauerhaft gespeichert. KI-Auswertung folgt im Hintergrund …';
  const restore=()=>{uploading=false;buttons.forEach(([button,disabled])=>button.disabled=disabled);};
  progress.removeAttribute('value');
  try{
   const response=await ProjektakteNet.requestJSON(form.action,{method:'POST',body:data,headers:{'X-Requested-With':'XMLHttpRequest'},signal:AbortSignal.timeout(120000)}),result=response.data;
   if(response.ok&&result?.url){try{await cacheWrites;if(key)await (await cache()).delete(key);}catch{}location.assign(result.url);return;}
   restore();message.textContent=result?.error||'Speicherung nicht bestätigt. Das lokale Paket bleibt erhalten.';
  }catch{
   restore();ProjektakteNet.loginNotice(message,location.href,'Speicherung nicht bestätigt. Das lokale Paket bleibt erhalten; erneutes Senden erzeugt keinen zweiten Eintrag. ');
  }
 });
 window.addEventListener('pagehide',revoke);
});
