(() => {
 const form=document.querySelector('[data-repair-advice]');if(!form)return;
 const button=form.querySelector('button'),status=form.querySelector('[role=status]'),result=form.querySelector('[data-advice-result]');
 function text(tag,value){const el=document.createElement(tag);el.textContent=value;return el;}
 form.addEventListener('submit',async event=>{
  event.preventDefault();if(button.disabled)return;button.disabled=true;result.replaceChildren();status.textContent='Originaltreffer werden eingeordnet …';
  try {
   const response=await ProjektakteNet.requestJSON(form.action,{method:'POST',body:new FormData(form),signal:AbortSignal.timeout(180000)});
   if(!response.ok)throw new Error(response.data.error||'Suchhilfe nicht verfügbar.');
   const data=response.data;result.append(text('p',data.orientation));
   if(data.question)result.append(text('p','Noch zu klären: '+data.question));
   data.matches.forEach((item,index)=>{
    const card=document.createElement('article');card.className='panel';
    card.append(text('small',index===0?'Zuerst ansehen · KI-Vorschlag':'Alternative · anderer Arbeitsumfang'));
    const heading=document.createElement('h3'),link=text('a',item.title);link.href=item.url;heading.append(link);card.append(heading);
    card.append(text('p',item.scope),text('p',item.reason));
    if(item.uncertainty)card.append(text('p','Bitte prüfen: '+item.uncertainty));
    const open=text('a','Originalanleitung öffnen →');open.href=item.url;open.className='btn';card.append(open);result.append(card);
   });
   status.textContent='KI-Orientierung · Arbeitsanweisungen und Drehmomente stehen im verlinkten Original.';
  } catch(error){
   if(error.name==='LoginRequired')ProjektakteNet.loginNotice(status,location.href);
   else status.textContent=error.message||'Verbindung unterbrochen. Die normale Suche bleibt verfügbar.';
  } finally{button.disabled=false;}
 });
})();
