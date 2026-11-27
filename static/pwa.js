(()=>{
 const t=window.t||(s=>s);
 if(new URL(location.href).searchParams.has('auth_refresh')){const clean=new URL(location.href);clean.searchParams.delete('auth_refresh');history.replaceState(null,'',clean);}
 const setStatus=text=>{
  const apply=()=>{const status=document.querySelector('[data-pwa-status]');if(status)status.textContent=text;};
  if(document.readyState==='loading')document.addEventListener('DOMContentLoaded',apply,{once:true});
  else apply();
 };
 if(!window.isSecureContext){
  setStatus(t('App-Installation und Android-Teilen-Menü erfordern HTTPS (oder localhost). Über HTTP im LAN kannst du Dateien direkt oben über die Dateiauswahl hochladen.'));
  return;
 }
 if(!('serviceWorker' in navigator)){
  setStatus(t('Dieser Browser unterstützt die App-Installation nicht. Du kannst Dateien direkt oben über die Dateiauswahl hochladen.'));
  return;
 }
 let installPrompt;
 window.addEventListener('beforeinstallprompt',event=>{event.preventDefault();installPrompt=event;document.querySelectorAll('[data-install-app]').forEach(button=>button.hidden=false);});
 window.addEventListener('appinstalled',()=>{installPrompt=null;document.querySelectorAll('[data-install-app]').forEach(button=>button.hidden=true);});
 document.addEventListener('DOMContentLoaded',()=>{
  document.querySelectorAll('[data-install-app]').forEach(button=>button.addEventListener('click',async()=>{if(!installPrompt)return;await installPrompt.prompt();await installPrompt.userChoice;installPrompt=null;button.hidden=true;}));
 });
 window.addEventListener('load',()=>navigator.serviceWorker.register('/sw.js',{scope:'/',updateViaCache:'none'}).then(()=>navigator.serviceWorker.ready).then(()=>{
  const status=document.querySelector('[data-pwa-status]');if(status)status.textContent=t('Teilen-Eingang bereit. Projektakte in Chrome als App installieren, danach aus Galerie oder PDF-App teilen.');
 }).catch(()=>{const status=document.querySelector('[data-pwa-status]');if(status)status.textContent=t('Teilen-Eingang konnte nicht aktiviert werden. Seite neu laden oder Dateien unten auswählen.');}));
})();
