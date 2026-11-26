const initVoice=()=>{
 const t=window.t||(s=>s);
 const start=document.getElementById('voice-start'),stop=document.getElementById('voice-stop'),file=document.getElementById('voice-file'),status=document.getElementById('voice-record-status');
 if(!start||start.dataset.voiceBound)return;
 start.dataset.voiceBound='1';
 const save=file.form?.querySelector('[data-capture-submit]');
 let recorder,stream,timer,chunks=[],size=0;
 const close=()=>{clearTimeout(timer);stream?.getTracks().forEach(track=>track.stop());start.disabled=false;stop.hidden=true;if(file.form)delete file.form.dataset.voiceRecording;if(save)save.disabled=false;};
 if(!window.isSecureContext||!navigator.mediaDevices?.getUserMedia||!window.MediaRecorder){start.disabled=true;status.textContent=t('Für Mikrofonaufnahme HTTPS und einen unterstützten Browser verwenden. Du kannst stattdessen eine Audio-Datei auswählen.');return;}
 start.addEventListener('click',async()=>{
  start.disabled=true;if(file.form)file.form.dataset.voiceRecording='1';if(save)save.disabled=true;
  try{
   stream=await navigator.mediaDevices.getUserMedia({audio:true});
   const mime=['audio/webm;codecs=opus','audio/webm','audio/mp4'].find(type=>MediaRecorder.isTypeSupported(type));
   recorder=new MediaRecorder(stream,mime?{mimeType:mime}:{});chunks=[];size=0;
   recorder.ondataavailable=event=>{if(event.data.size){size+=event.data.size;chunks.push(event.data);if(size>8*1024*1024&&recorder.state==='recording')recorder.stop();}};
   recorder.onstop=()=>{
    close();if(!size||size>8*1024*1024){status.textContent=t('Aufnahme leer oder zu groß. Bitte kürzer aufnehmen.');return;}
    try{const transfer=new DataTransfer();transfer.items.add(new File(chunks,['aufnahme',recorder.mimeType.includes('mp4')?'m4a':'webm'].join('.'),{type:recorder.mimeType}));file.files=transfer.files;file.dispatchEvent(new Event('change',{bubbles:true}));status.textContent=t('Aufnahme fertig. Mit „{label}“ hochladen.',{label:save?.textContent.trim()||t('Sprachnachricht speichern')});}catch{status.textContent=t('Aufnahme konnte nicht übernommen werden. Bitte Audio-Datei auswählen.');}
   };
   recorder.onerror=()=>{close();status.textContent=t('Aufnahme fehlgeschlagen. Audio-Datei auswählen oder erneut versuchen.');};
   recorder.start(1000);stop.hidden=false;status.textContent=t('Aufnahme läuft …');timer=setTimeout(()=>{if(recorder.state==='recording')recorder.stop();},180000);
  }catch{close();status.textContent=t('Mikrofonzugriff nicht möglich. Berechtigung prüfen oder Audio-Datei hochladen.');}
 });
 stop.addEventListener('click',()=>{if(recorder?.state==='recording')recorder.stop();});
 window.addEventListener('pagehide',()=>{if(recorder?.state==='recording')recorder.stop();close();});
};
document.addEventListener('DOMContentLoaded',initVoice);
document.addEventListener('lg:navigate',initVoice);
if(document.readyState!=='loading')initVoice();
