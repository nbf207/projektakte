document.addEventListener('DOMContentLoaded',()=>{
 const dialog=document.getElementById('feedback-dialog'),form=document.getElementById('feedback-form');if(!dialog||!form)return;
 const status=document.getElementById('feedback-status');let sending=false;
 document.getElementById('feedback-open').addEventListener('click',()=>{form.elements.url.value=location.href;form.elements.title.value=document.title.slice(0,200);dialog.showModal();requestAnimationFrame(()=>{const input=form.elements.text;input.focus({preventScroll:true});input.setSelectionRange(input.value.length,input.value.length);});});
 document.getElementById('feedback-close').addEventListener('click',()=>dialog.close());
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(sending)return;sending=true;const button=form.querySelector('[type=submit]');button.disabled=true;
  try{const response=await ProjektakteNet.requestJSON(form.action,{method:'POST',body:new FormData(form),headers:{'X-Requested-With':'XMLHttpRequest'},signal:AbortSignal.timeout(15000)});
   status.textContent=response.data.message||response.data.error||'Feedback nicht bestätigt.';if(response.ok){form.elements.text.value='';form.elements.image.value='';form.elements.image.dispatchEvent(new Event('change'));}
  }catch{status.textContent='Verbindung oder Anmeldung unterbrochen. Deine Notiz bleibt im Eingabefeld.';}
  finally{sending=false;button.disabled=false;}
 });
});
