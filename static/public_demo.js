(() => {
  'use strict';
  const page = document.body.dataset.demoPage;

  const prefix = 'projektakte-tours-v2-' + document.body.dataset.demoVisitor + '-' + document.body.dataset.demoGeneration;
  const get = name => { try { return sessionStorage.getItem(prefix+name); } catch { return null; } };
  const put = (name,value) => { try { sessionStorage.setItem(prefix+name,String(value)); } catch {} };
  const tours = {
    oil: {name:'Vom Ölwechsel zur fertigen Akte', steps:[
      ['home','#demo-capture','Du hast einen Ölwechsel gemacht.','Die Rechnung liegt noch auf dem Tisch. Schau sie dir an und setze sie mit einem Klick ein.','invoice','Rechnung ansehen'],
      ['home','#demo-capture-form','Foto und ein Satz reichen.','Die Rechnung ist eingesetzt. Die Notiz ergänzt, was du gemacht hast und bei welchem Kilometerstand. Jetzt darf die App sortieren.','capture','Erfassen & zuordnen'],
      ['chronik','.demo-entry','Deine Arbeit ist festgehalten.','Hier stehen Datum, Kilometerstand und Öl-Angaben zusammen. Später kannst du Arbeitsfotos ergänzen. Die Rechnung ist gespeichert, aber noch nicht als Kosten bestätigt.'],
      ['belege','.demo-review-example','Du behältst die Kontrolle.','Vergleiche das Original links mit den Angaben rechts. Mit deiner Bestätigung werden die 89,50 € genau einmal zugeordnet.','review','Angaben bestätigen'],
      ['wartung','#demo-maintenance .demo-entry','Der nächste Ölwechsel bleibt im Blick.','Das Intervall ist hinterlegt. Die bestätigte Wartung setzt den Ausgangspunkt auf deinen neuen Eintrag. Fertig: festhalten, prüfen, rechtzeitig erinnern.']
    ]},
    ai: {name:'Sprich es einfach ein', steps:[
      ['home','#demo-ai-lab','Sag es, wie du es erzählen würdest.','Höre eine vorbereitete Sprachnotiz an. Du brauchst kein Formular auszufüllen. Die folgende Erkennung ist ein vorbereitetes Beispiel, kein echter KI-Aufruf.','audio','Beispiel anhören'],
      ['home','#demo-ai-lab','Aus Sprache werden klare Angaben.','Projekt und Kategorie wurden vorgeschlagen. Ein Stern bedeutet: interpretiert. Betrag und Datum bleiben leer, weil sie nicht genannt wurden. Du kannst den Titel hier korrigieren.'],
      ['home','#demo-ai-lab','Auch eine Rechnung liefert Informationen.','Dieses vorbereitete Beispiel zeigt, wie eine Rechnung Projekt, Händler und Betrag liefern kann. Die Erkennung ist simuliert; Kosten werden noch nicht gebucht.'],
      ['home','#demo-capture-form','Aus einer Notiz wird eine Aufgabe.','„Dachträger vor dem Urlaub kontrollieren.“ So kurz darf es sein. Wir speichern diese Notiz jetzt in deinem eigenen Demo-Bestand.','capture','Notiz speichern'],
      ['chronik','.demo-entry','Gespeichert und wieder auffindbar.','Die Notiz ist jetzt in deiner Akte. Aus dem noch offenen Vorhaben wurde zusätzlich eine Aufgabe.'],
      ['aufgaben','#demo-tasks','Du entscheidest später, wann es erledigt wird.','Die neue Aufgabe hat absichtlich keinen erfundenen Termin. In der persönlichen App kannst du ihn ergänzen und die Aufgabe abhaken.']
    ]},
    overview: {name:'Dein Projekt im Überblick', steps:[
      ['chronik','.demo-entry[data-kind="umbau"]','Ein Umbau wächst mit deinem Projekt.','Hier wurde ein Dachträger montiert. Beschreibung, Beleg und Kosten gehören zu derselben Arbeit. Öffne nicht mehrere Ablagen: alles bleibt am Eintrag.'],
      ['chronik','.demo-entry[data-kind="umbau"]','Bilder erklären mehr als lange Notizen.','Füge ein vorbereitetes Werkstattfoto zur Dokumentation hinzu. Dieses Foto ist nur ein Muster; es zeigt nicht den tatsächlichen Dachträger.','photo','Beispielfoto hinzufügen'],
      ['suche','#demo-search','Ein Wort bringt dich zurück.','Wir suchen nach „Bremsen“. Die Treffer führen zurück zur Chronik, auch wenn die Arbeit schon länger her ist.'],
      ['chronik','.demo-stats','Kosten bleiben nachvollziehbar.','Diese Summe enthält bestätigte Zuordnungen. Ungeprüfte Rechnungen zählen noch nicht dazu. So bleibt dieselbe Rechnung nicht zweimal in der Summe.'],
      ['export','#demo-export','Deine Dokumentation kann mitkommen.','Hier ist ein druckbarer Bericht. „Drucken / als PDF speichern“ öffnet den Druckdialog. Du kannst ihn jetzt ausprobieren oder die Tour abschließen.']
    ]}
  };
  let active = get(':active');
  if(!tours[active])active=null;
  let currentTour=null, generation=0, leaving=false;
  const read = () => active ? get(':'+active) : null;
  const write = value => { if(active)put(':'+active,value); };
  const stop = () => {window.speechSynthesis?.cancel();generation++;leaving=true;currentTour?.destroy();currentTour=null;leaving=false;};
  function selection(){
    stop();put(':running','0');
    document.body.classList.remove('demo-guided');
    document.body.classList.add('demo-selecting');
    document.querySelector('#demo-tour-picker').hidden=false;
    document.querySelectorAll('[data-tour-choice]').forEach(button=>{
      const value=get(':'+button.dataset.tourChoice);
      button.textContent=value==='done'?'Noch einmal erleben':value!==null?'Fortsetzen':'Tour starten';
    });
    document.querySelector('#demo-tour-picker h1')?.focus();
  }
  function explore(){
    stop();put(':running','0');put(':visited','1');
    document.body.classList.remove('demo-guided','demo-selecting');
    document.querySelector('#demo-tour-picker').hidden=true;
    document.querySelector('#demo-guide-bar').hidden=true;
  }
  function go(index){
    stop();write(index);put(':running','1');put(':visited','1');
    const step=tours[active].steps[index];
    if(step[0]===page)tour(index);else location.assign('/demo/'+step[0]+'/');
  }
  function start(id,restart=false){
    active=id;put(':active',id);
    const saved=get(':'+id);
    go(!restart && saved!==null && /^d+$/.test(saved) && tours[id].steps[Number(saved)] ? Number(saved):0);
  }
  function animateFocus(target){
    target?.animate?.([{outline:'2px solid transparent',outlineOffset:'8px'},{outline:'2px solid var(--accent)',outlineOffset:'4px'},{outline:'2px solid transparent',outlineOffset:'0'}],{duration:1100});
    [...(target?.querySelectorAll('.demo-meta>span,.demo-entry-content>p,.badge,:scope>p,.demo-entry')||[])].slice(0,5).forEach((row,i)=>row.animate?.([{opacity:.2,transform:'translateY(8px)'},{opacity:1,transform:'translateY(0)'}],{duration:400,delay:i*120}));
  }
  function finish(){
    write('done');selection();
    document.querySelector('#demo-picker-intro').textContent='Geschafft! Wähle einen weiteren Ablauf oder erkunde deinen Demo-Workspace selbst.';
  }
  function prepare(index){
    const lab=document.querySelector('#demo-ai-lab');
    if(lab)lab.hidden=active!=='ai'||index>2;
    if(active==='oil'&&page==='home'&&index===1&&!document.querySelector('.demo-invoice-card'))insertExample();
    if(active==='ai'&&page==='home'){
      if(index===0)lab.innerHTML='<h2>Eine kurze Sprachnotiz</h2><p>Vorbereitetes Audio · keine echte KI-Verarbeitung</p><p id="demo-speech-status" role="status">Klicke auf „Beispiel anhören“. Dein Browser liest den vorbereiteten Text vor.</p><button type="button" id="demo-audio-skip">Ohne Ton weiter</button>';
      if(index===0)document.querySelector('#demo-audio-skip').onclick=()=>go(1);
      if(index===1)lab.innerHTML='<h2>Das wurde gesagt</h2><p>„Beim Reisekombi vorne die Bremsen gewechselt. Kilometerstand 128450. Die Probefahrt war ruhig.“</p><div class="demo-result-grid"><label>Titel – probiere eine Korrektur<input value="Bremsen vorne gewechselt" maxlength="150"></label><p>Projekt<br><strong>Nordlicht · Reisekombi *</strong></p><p>Kategorie<br><strong>Reparatur *</strong></p><p>Kilometerstand<br><strong>128450</strong></p><p>Datum<br><strong>— nicht genannt</strong></p><p>Betrag<br><strong>— nicht genannt</strong></p></div><small>* Interpretiert. Dies ist eine Vorschau, noch kein gespeicherter Eintrag.</small>';
      if(index===2)lab.innerHTML='<h2>Eine Rechnung als Eingabe</h2><a class="demo-invoice-card" href="/static/demo/musterrechnung.pdf" target="_blank" rel="noopener"><small>NORDLICHT WERKSTATT · MUSTER</small><strong>Ölwechsel & Inspektion</strong><span>Motoröl · Filter · Arbeitszeit</span><b>89,50 €</b><small>Original öffnen ↗</small></a><p>Vorbereiteter Vorschlag: Nordlicht · Wartung · Ölwechsel · 89,50 €</p>';
      if(index===3){document.querySelector('#demo-note').value='Dachträger vor dem Urlaub kontrollieren.';document.querySelector('#demo-example-assets').value='0';document.querySelector('#demo-previews').replaceChildren();}
    }
    if(active==='overview'&&page==='suche'&&!new URL(location.href).searchParams.has('q')){
      const url=new URL(location.href);url.searchParams.set('q','Bremsen');location.replace(url);return false;
    }
    return true;
  }
  function tour(index){
    if(!active||!tours[active].steps[index]||tours[active].steps[index][0]!==page)return;
    document.body.classList.remove('demo-selecting');
    document.body.classList.add('demo-guided');
    document.querySelector('#demo-tour-picker').hidden=true;
    const bar=document.querySelector('#demo-guide-bar');bar.hidden=false;
    bar.querySelector('strong').textContent=tours[active].name;
    bar.querySelector('span').textContent='Schritt '+(index+1)+' von '+tours[active].steps.length;
    const ticket=++generation;
    setTimeout(()=>{
      if(ticket!==generation||!prepare(index))return;
      const [,selector,title,description,action,label]=tours[active].steps[index];
      const target=document.querySelector(selector);
      if(!target){selection();return;}
      if(target.matches('details'))target.open=true;
      document.querySelectorAll('.demo-tour-focus').forEach(el=>el.classList.remove('demo-tour-focus'));
      target.classList.add('demo-tour-focus');
      target.scrollIntoView({block:'center',behavior:'instant'});
      const last=index===tours[active].steps.length-1;
      const instance=window.driver?.js?.driver({
        animate:true,overlayOpacity:.42,stagePadding:8,stageRadius:16,allowClose:true,popoverClass:'demo-tour',
        onDestroyStarted:()=>{instance.destroy();if(!leaving)selection();},
        steps:[{element:selector,popover:{title,description,side:'bottom',align:'start',
          showButtons:index?['previous','next','close']:['next','close'],
          prevBtnText:'Zurück',nextBtnText:(action==='photo'&&!target.querySelector('form[data-demo-photo]')?'Weiter':label)||(last?'Tour abschließen':'Weiter'),
          onPrevClick:()=>go(index-1),
          onNextClick:async()=>{
            if(action==='invoice'){stop();showExample(()=>go(index+1));return;}
            if(action==='capture'){stop();document.querySelector('#demo-capture-form').requestSubmit();return;}
            if(action==='audio'){
              if(!window.speechSynthesis||!window.SpeechSynthesisUtterance){go(index+1);return;}
              window.speechSynthesis.cancel();
              const speech=new SpeechSynthesisUtterance('Beim Reisekombi vorne die Bremsen gewechselt. Kilometerstand 128450. Die Probefahrt war ruhig.');
              speech.lang='de-DE';speech.rate=.95;
              document.querySelector('#demo-speech-status').textContent='Beispiel wird vorgelesen …';
              speech.onend=()=>{if(ticket===generation&&get(':running')==='1'&&Number(read())===index)go(index+1);};
              speech.onerror=()=>{const status=document.querySelector('#demo-speech-status');if(status)status.textContent='Vorlesen nicht verfügbar. Mit „Ohne Ton weiter“ geht es weiter.';};
              window.speechSynthesis.speak(speech);
              return;
            }
            if(action==='review'){
              const review=target.querySelector('form');
              if(review){stop();review.requestSubmit();return;}
            }
            if(action==='photo'){const photoForm=target.querySelector('form[data-demo-photo]');if(photoForm){stop();photoForm.requestSubmit();}else go(index+1);return;}
            if(last)finish();else go(index+1);
          }
        }}]
      });
      if(instance){currentTour=instance;instance.drive();animateFocus(target);}
    },180);
  }
  document.querySelectorAll('[data-tour-choice]').forEach(button=>button.addEventListener('click',()=>start(button.dataset.tourChoice)));
  document.querySelectorAll('[data-tour-restart]').forEach(button=>button.addEventListener('click',()=>start(button.dataset.tourRestart,true)));
  document.querySelector('#demo-tour-start')?.addEventListener('click',()=>active&&read()!=='done'?start(active):selection());
  document.querySelector('#demo-tour-menu').addEventListener('click',selection);
  document.querySelector('#demo-guide-exit').addEventListener('click',selection);
  document.querySelector('#demo-explore').addEventListener('click',explore);
  document.querySelectorAll('form[data-demo-review],form[data-demo-photo]').forEach(form=>form.addEventListener('submit',()=>{
    stop();
    form.querySelector('button').disabled=true;
  }));
  window.addEventListener('pagehide',()=>{window.speechSynthesis?.cancel();});
  document.addEventListener('click',event=>{
    if(event.target.closest('.sidebar a,.product-wordmark')&&get(':running')==='1'){stop();put(':running','0');}
  });
  const url=new URL(location.href);
  const landing=url.searchParams.has('tour');
  if(landing){url.searchParams.delete('tour');history.replaceState(null,'',url);}
  if(landing||!get(':visited'))selection();
  else if(active&&get(':running')==='1'&&read()!=='done'){
    const index=Number(read());
    if(url.searchParams.has('confirmed')&&tours[active].steps[index]?.[4]==='review')go(index+1);
    else if(tours[active].steps[index]?.[0]===page)tour(index);
    else {put(':running','0');explore();}
  }
  document.querySelector('#demo-print')?.addEventListener('click', () => window.print());
  const form = document.querySelector('#demo-capture-form');
  if (!form) return;
  const note = document.querySelector('#demo-note');
  const status = document.querySelector('#demo-upload-status');
  const files = document.querySelector('#demo-files');
  const submit = form.querySelector('[type=submit]');
  const nonce = () => crypto.randomUUID().replaceAll('-', '');
  document.querySelector('#demo-request-key').value = nonce();
  function insertExample(){note.value = 'Heute Ölwechsel bei 128450 km. 4,5 Liter 5W-30, neuer Ölfilter. Rechnung 89,50 €.'; document.querySelector('#demo-example-assets').value='1';const previews=document.querySelector('#demo-previews');previews.replaceChildren();const card=document.createElement('a');card.className='demo-invoice-card';card.href='/static/demo/musterrechnung.pdf';card.target='_blank';card.rel='noopener';card.innerHTML='<small>NORDLICHT WERKSTATT · MUSTERRECHNUNG</small><strong>Ölwechsel & Inspektion</strong><span>Motoröl 5W-30 · 4,5 Liter &nbsp; 37,82 €<br>Ölfilter &nbsp; 12,60 €<br>Arbeitszeit &nbsp; 24,79 €</span><span>Netto 75,21 € · MwSt. 19 %: 14,29 €</span><b>Gesamt 89,50 €</b><small>PDF öffnen ↗</small>';previews.append(card);return card;}
  let exampleDialog=null;
  function showExample(afterInsert){
    if(exampleDialog)return;
    const dialog=document.createElement('dialog');exampleDialog=dialog;dialog.className='demo-insert-dialog';
    const heading=document.createElement('h2');heading.textContent='Deine Rechnung. Gleich in deiner Akte.';
    const paper=document.createElement('div');paper.className='demo-invoice-card demo-invoice-large';
    paper.innerHTML='<small>NORDLICHT WERKSTATT · MUSTERRECHNUNG</small><strong>Rechnung DEMO-2026-042</strong><span>Ölwechsel & Inspektion<br>Projektfahrzeug Nordlicht · 128450 km</span><hr><span>Motoröl 5W-30 · 4,5 Liter <b>37,82 €</b><br>Ölfilter <b>12,60 €</b><br>Arbeitszeit / Kontrolle <b>24,79 €</b></span><span>Netto 75,21 €<br>19 % MwSt. 14,29 €</span><b>Gesamt 89,50 €</b><small>Fiktives Beispiel · keine echte Zahlungsforderung</small>';
    const actions=document.createElement('div');actions.className='demo-actions';
    const insert=document.createElement('button');insert.type='button';insert.textContent='Rechnung einsetzen →';
    const close=document.createElement('button');close.type='button';close.className='ghost';close.textContent='Abbrechen';
    const cleanup=()=>{dialog.close();dialog.remove();exampleDialog=null;document.querySelector('#demo-example')?.focus({preventScroll:true});};
    close.onclick=cleanup;dialog.addEventListener('cancel',event=>{event.preventDefault();if(!insert.disabled)cleanup();});
    insert.onclick=async()=>{
      insert.disabled=true;close.disabled=true;
      const start=paper.getBoundingClientRect();
      const card=insertExample();card.style.visibility='hidden';card.scrollIntoView({block:'center',behavior:'instant'});
      await new Promise(resolve=>requestAnimationFrame(resolve));
      const target=card.getBoundingClientRect();
      heading.style.visibility='hidden';actions.style.visibility='hidden';
      Object.assign(dialog.style,{position:'fixed',inset:'0',width:'100vw',height:'100dvh',maxWidth:'none',maxHeight:'none',margin:'0',padding:'0',overflow:'visible',background:'transparent',border:'0',boxShadow:'none'});
      Object.assign(paper.style,{position:'fixed',left:start.left+'px',top:start.top+'px',width:start.width+'px',height:start.height+'px',margin:'0',transformOrigin:'top left'});
      try{
        if(paper.animate)await paper.animate([
          {transform:'translate(0,0) scale(1)',opacity:1},
          {transform:`translate(${target.left-start.left}px,${target.top-start.top}px) scale(${target.width/start.width},${target.height/start.height})`,opacity:.85}
        ],{duration:720,easing:'cubic-bezier(.22,1,.36,1)',fill:'forwards'}).finished;
      }finally{
        card.style.visibility='';cleanup();card.focus({preventScroll:true});
        card.animate?.([{boxShadow:'0 0 0 3px var(--accent)'},{boxShadow:'0 12px 32px #0003'}],{duration:650});
        if(afterInsert)afterInsert();
      }
    };
    actions.append(insert,close);dialog.append(heading,paper,actions);document.body.append(dialog);dialog.showModal();insert.focus();
  }
  document.querySelector('#demo-example').addEventListener('click',()=>showExample());

  let audio = null, recorder = null, stream = null, timer = null, chunks = [], total = 0;
  let previewUrls = [];
  const stopStream = () => { stream?.getTracks().forEach(track => track.stop()); clearTimeout(timer); };
  const record = document.querySelector('#demo-record');
  record.addEventListener('click', async () => {
    if (recorder?.state === 'recording') { recorder.stop(); return; }
    if (!navigator.mediaDevices?.getUserMedia || !window.MediaRecorder) { status.textContent = 'Mikrofon nicht verfügbar. Bitte Audio-Datei auswählen.'; return; }
    try {
      stream = await navigator.mediaDevices.getUserMedia({audio: true});
      const mime = ['audio/webm;codecs=opus','audio/mp4'].find(type => MediaRecorder.isTypeSupported(type));
      recorder = new MediaRecorder(stream, mime ? {mimeType: mime} : undefined);
      chunks = []; total = 0; audio = null;
      recorder.ondataavailable = event => { total += event.data.size; if (total <= 1500000) chunks.push(event.data); else if (recorder.state === 'recording') recorder.stop(); };
      recorder.onstop = () => { stopStream(); audio = new File(chunks, mime?.includes('mp4') ? 'demo-audio.m4a' : 'demo-audio.webm', {type: recorder.mimeType}); record.textContent = 'Neu aufnehmen'; status.textContent = 'Aufnahme bereit. In der Demo wird Audio nicht transkribiert. Ergänze eine Notiz oder nutze das Textbeispiel.'; };
      recorder.onerror = () => { stopStream(); status.textContent = 'Aufnahme abgebrochen. Audio-Datei-Upload ist weiterhin möglich.'; };
      recorder.start(250); record.textContent = 'Aufnahme stoppen'; status.textContent = 'Aufnahme läuft · maximal 30 Sekunden.';
      timer = setTimeout(() => { if (recorder.state === 'recording') recorder.stop(); }, 30000);
    } catch { stopStream(); status.textContent = 'Kein Mikrofonzugriff. Du kannst eine Audio-Datei auswählen.'; }
  });
  files.addEventListener('change', () => {
    previewUrls.forEach(URL.revokeObjectURL); previewUrls = [];
    const previews = document.querySelector('#demo-previews'); previews.replaceChildren();
    for (const file of [...files.files].slice(0,4)) {
      const label = document.createElement('span'); label.textContent = file.name; previews.append(label);
      if (file.size <= 2000000 && ['image/jpeg','image/png','image/webp'].includes(file.type)) {
        const image = document.createElement('img'); image.alt = file.name; image.src = URL.createObjectURL(file); previewUrls.push(image.src); previews.append(image);
      }
    }
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (submit.disabled) return;
    if (recorder?.state === 'recording') { status.textContent = 'Bitte zuerst die Aufnahme stoppen.'; return; }
    const all = [...files.files, ...(audio ? [audio] : [])];
    if (!note.value.trim() && !all.length) { note.focus(); status.textContent = 'Bitte eine Notiz oder Datei ergänzen.'; return; }
    if (all.length > 4 || all.reduce((sum,file) => sum+file.size,0) > 2000000) { status.textContent = 'Bitte maximal vier Dateien mit insgesamt weniger als 2 MB wählen.'; return; }
    const data = new FormData(form); if (audio) data.append('files',audio);
    submit.disabled = true;form.setAttribute('aria-busy','true');status.classList.add('demo-processing');status.tabIndex=-1;status.textContent='Eingabe wird verarbeitet … · Demo-Simulation';status.focus({preventScroll:true});status.scrollIntoView({block:'center',behavior:'smooth'});stop();
    try {
      await new Promise(resolve=>setTimeout(resolve,1200));
      status.textContent='Projekt, Betrag und Zusatzfelder werden zugeordnet … · Simulation';
      await new Promise(resolve=>setTimeout(resolve,1000));
      const response = await fetch(form.action, {method:'POST',body:data,credentials:'same-origin',redirect:'follow'});
      if (!response.ok) { status.textContent = response.status === 429 ? 'Bitte kurz warten oder die Demo zurücksetzen.' : 'Nicht gespeichert. Sitzung, Dateityp oder Größenlimit prüfen.'; return; }
      const destination = new URL(response.url);
      if (destination.origin !== location.origin || !destination.pathname.startsWith('/demo/chronik/')) { status.textContent = 'Deine Sitzung ist abgelaufen. Öffne die Anmeldung erneut; die Eingabe bleibt hier erhalten.'; return; }
      if(active&&get(':running')==='1')write(Number(read())+1);
      location.assign(destination.href);
    } catch { status.textContent = 'Verbindung unterbrochen. Du kannst erneut speichern; dieselbe Erfassung wird nicht doppelt angelegt.'; }
    finally { submit.disabled = false;form.removeAttribute('aria-busy');status.classList.remove('demo-processing'); }
  });
  window.addEventListener('pagehide', () => { stopStream(); previewUrls.forEach(URL.revokeObjectURL); });
})();
