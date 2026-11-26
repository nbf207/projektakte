function initEntryEditor(){
 const data=document.getElementById('entry-definitions');if(!data)return;
 const root=data.closest('[data-entry-editor]');if(!root||root.dataset.ready)return;root.dataset.ready='1';
 const control=name=>root.querySelector(`[data-entry-field="${name}"] input, [data-entry-field="${name}"] select`);
 const category=control('category'),title=control('title'),template=control('title_template'),suffix=control('title_suffix');
 const prefix=title.name.slice(0,-'title'.length),area=root.querySelector('#entry-extra-fields'),grid=area.querySelector('.formgrid');
 const definitions=JSON.parse(data.textContent),groups=definitions.flatMap(p=>p.categories),projectFields=definitions.flatMap(p=>p.fields||[]);
 const templates=new Map(),marks=new Map(JSON.parse(document.getElementById('entry-field-marks')?.textContent||'[]').map(r=>[r.id,r]));
 groups.forEach(g=>g.titles.forEach(t=>templates.set(t.id,{...t,category:g.id})));
 const values=new Map();let freeTitle=title.value;
 const fieldKey=id=>'extra_'+id.replaceAll('-','');
 const hidden=name=>root.querySelector(`input[name="${prefix+name}"]`);
 function render(){
  grid.querySelectorAll('input:not([type=hidden])').forEach(input=>values.set(input.name,input.value));
  const group=groups.find(g=>g.id===category.value),wanted=template.value;
  template.replaceChildren(new Option('Eigenen Titel eingeben',''));
  for(const item of group?.titles||[])template.add(new Option(item.name,item.id));
  template.value=(group?.titles||[]).some(t=>t.id===wanted)?wanted:'';
  const selected=templates.get(template.value),fields=[...projectFields,...(selected?.fields||[])];
  hidden('category_version').value=group?.version||'';hidden('template_version').value=selected?.version||'';
  // Keep all four slots in place, including when no template is selected.
  title.readOnly=!!selected;suffix.disabled=!selected;
  title.value=selected?(selected.name+' '+suffix.value.trim()).trim():freeTitle;
  area.hidden=!fields.length;grid.replaceChildren();
  for(const field of fields){
   const label=document.createElement('label');label.textContent=field.label+(field.unit?' ('+field.unit+')':'');
   const input=document.createElement('input');input.name=prefix+fieldKey(field.id);input.maxLength=2000;
   input.value=values.get(input.name)??marks.get(field.id)?.value??'';input.placeholder=field.placeholder||'';
   const original=marks.get(field.id);
   if(original?.inferred){const mark=document.createElement('span');mark.className='ai-inferred';mark.textContent='*';mark.title='Von der KI interpretiert; bitte prüfen';const update=()=>mark.hidden=input.value!==original.value;update();input.addEventListener('input',update);label.append(mark);}
   if(field.type==='number')input.inputMode='decimal';
   if(field.suggestions?.length){
    const list=document.createElement('datalist');list.id='recent-'+field.id;
    for(const value of field.suggestions)list.append(new Option(value));
    input.setAttribute('list',list.id);label.append(list);
    const choices=document.createElement('div');choices.className='recent-field-values';choices.hidden=true;
    for(const value of field.suggestions){const button=document.createElement('button');button.type='button';button.className='ghost';button.textContent=value;button.addEventListener('click',()=>{input.value=value;input.dispatchEvent(new Event('input',{bubbles:true}));choices.hidden=true;});choices.append(button);}
    input.addEventListener('focus',()=>choices.hidden=false);input.addEventListener('keydown',event=>{if(event.key==='Escape')choices.hidden=true;});label.append(choices);
   }
   label.append(input);const version=document.createElement('input');version.type='hidden';version.name=prefix+'extra_version_'+field.id.replaceAll('-','');version.value=field.version;label.append(version);grid.append(label);
  }
 }
 title.addEventListener('input',()=>{if(!template.value)freeTitle=title.value;});
 category.addEventListener('change',render);template.addEventListener('change',render);
 suffix.addEventListener('input',()=>{const selected=templates.get(template.value);if(selected)title.value=(selected.name+' '+suffix.value.trim()).trim();});
 render();
}
document.addEventListener('DOMContentLoaded',initEntryEditor);
