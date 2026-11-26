document.addEventListener('DOMContentLoaded',()=>{
 const form=document.querySelector('#review-form'),project=form?.querySelector('[name=chronicle_project]');if(!project)return;
 const area=document.querySelector('#review-entry-fields');let pending=false,loadFailed=false,sequence=0;
 const initialGross=form.querySelector('[name=gross_total]').value;const normalize=value=>value.replace(',','.').trim();
 const error=document.createElement('p');error.className='helptext';error.setAttribute('role','status');project.after(error);
 function mainAllocation(){
  const rows=form.querySelectorAll('.allocation-row');if(rows.length!==1)return;
  rows[0].querySelector('[name=alloc_project]').value=project.value;
  const entry=rows[0].querySelector('[name=alloc_entry]');const option=entry?.selectedOptions[0];if(option&&entry.value&&!option.textContent.includes(project.selectedOptions[0].textContent))entry.value='';
  if(!form.querySelector('[name=connect]')?.value){const amount=rows[0].querySelector('[name=alloc_amount]');if(!amount.value||normalize(amount.value)===normalize(initialGross))amount.value=form.querySelector('[name=gross_total]').value;}
 }
 project.addEventListener('change',async()=>{
  if(!area){error.textContent='Das Projekt des bestehenden Eintrags bitte über „Eintrag bearbeiten“ ändern.';return;}
  if(!project.value)return;
  const current=++sequence;pending=true;loadFailed=false;form.querySelectorAll('button[type=submit],button[name=action]').forEach(b=>b.disabled=true);
  const url=new URL(location.href);url.searchParams.set('entry_project',project.value);
  try{
   const response=await fetch(url,{credentials:'same-origin',headers:{Accept:'application/json'}});
   if(!response.ok||response.redirected)throw Error();const data=await response.json();if(current!==sequence)return;
   const previous=new Map([...area.querySelectorAll('input,textarea,select')].filter(e=>!e.name.includes('extra_')&&!['chronicle-category','chronicle-title_template','chronicle-title_suffix','chronicle-category_version','chronicle-template_version'].includes(e.name)).map(e=>[e.name,e.value]));
   area.innerHTML=data.html;
   area.querySelectorAll('input,textarea,select').forEach(e=>{if(previous.has(e.name))e.value=previous.get(e.name);});
   initEntryEditor();mainAllocation();error.textContent='Kategorie, Titel-Vorlagen und Zusatzfelder aktualisiert.';
  }catch{if(current!==sequence)return;loadFailed=true;error.textContent='Felder konnten nicht geladen werden. Bitte Projekt erneut auswählen.';return;}
  finally{if(current===sequence){pending=false;form.querySelectorAll('button[name=action]').forEach(b=>b.disabled=loadFailed);}}
 });
 form.addEventListener('submit',event=>{if(pending||loadFailed){event.preventDefault();return;}if(!document.querySelector('#review-allocation')?.open)mainAllocation();});
});
