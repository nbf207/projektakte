/* Only OS share envelopes are cached. No account pages or API responses. */
const SHARE_CACHE='projektakte-shares-v1';
const SHARE_PREFIX='/__share-cache__/';
const SHARE_TTL=24*60*60*1000;
const MAX_SHARE_BYTES=12*1024*1024;
let intakeQueue=Promise.resolve();

self.addEventListener('install',event=>event.waitUntil(self.skipWaiting()));
self.addEventListener('activate',event=>event.waitUntil(self.clients.claim()));

async function pruneShares(cache){
 for(const key of await cache.keys()){
  const stored=await cache.match(key),created=Number(stored?.headers.get('X-Share-Created'));
  if(!created||Date.now()-created>SHARE_TTL)await cache.delete(key);
 }
}

async function receiveShare(request){
 const landing=new URL('/teilen/',self.location.origin);
 try{
  const data=await request.formData();
  const files=data.getAll('files').filter(file=>typeof file!=='string'&&file.size>0);
  if(!files.length||files.length>8||files.some(file=>file.size>8*1024*1024)||files.reduce((total,file)=>total+file.size,0)>MAX_SHARE_BYTES){landing.searchParams.set('fehler','dateien');return Response.redirect(landing.href,303);}
  if(files.some(file=>!(file.type.startsWith('image/')||file.type==='application/pdf'||/\.(jpe?g|png|webp|gif|heic|heif|pdf)$/i.test(file.name)))){landing.searchParams.set('fehler','format');return Response.redirect(landing.href,303);}
  const cache=await caches.open(SHARE_CACHE);await pruneShares(cache);
  if((await cache.keys()).length>=5){landing.searchParams.set('fehler','voll');return Response.redirect(landing.href,303);}
  const id=crypto.randomUUID(),payload=new FormData();
  for(const file of files)payload.append('files',file,file.name);
  payload.set('text',['title','text','url'].map(name=>data.get(name)).filter(value=>typeof value==='string'&&value.trim()).join('\n').slice(0,8000));
  await cache.put(new Request(new URL(SHARE_PREFIX+id,self.location.origin)),new Response(payload,{headers:{'X-Share-Created':String(Date.now()),'X-Share-Count':String(files.length)}}));
  landing.searchParams.set('paket',id);
 }catch{landing.searchParams.set('fehler','speicher');}
 return Response.redirect(landing.href,303);
}

self.addEventListener('fetch',event=>{
 const url=new URL(event.request.url);
 if(url.origin!==self.location.origin||url.pathname!=='/teilen/')return;
 if(event.request.method==='GET'&&url.searchParams.get('auth_refresh')==='1')return;
 if(event.request.method==='POST'){
  const response=intakeQueue.then(()=>receiveShare(event.request));
  intakeQueue=response.catch(()=>{});event.respondWith(response);
 }else if(event.request.method==='GET'){
  event.respondWith((async()=>{
   try{
    const response=await fetch(event.request,{credentials:'same-origin',redirect:'manual'});
    if(!ProjektakteNet.needsLogin(response))return response;
   }catch{}
   const resume=new URL('/teilen/',self.location.origin),packet=url.searchParams.get('paket');
   if(/^[a-f0-9-]{36}$/.test(packet||''))resume.searchParams.set('paket',packet);
   resume.searchParams.set('auth_refresh','1');
   const href=resume.href.replaceAll('&','&amp;').replaceAll('"','&quot;');
   return new Response('<!doctype html><html lang="de"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Projektakte – Anmeldung erneuern</title><main style="max-width:34em;margin:10vh auto;padding:24px;font:18px system-ui"><h1>Dateien bleiben im Browser</h1><p>Verbindung oder Anmeldung unterbrochen. Deine Dateien bleiben lokal erhalten. Nach der Google-Anmeldung kannst du das Paket fortsetzen.</p><a id="access-refresh" href="'+href+'" style="display:block;padding:16px;background:#0b625e;color:white;border-radius:10px">Anmeldung erneuern und fortsetzen</a><p>Falls du offline bist, zuerst die Verbindung wiederherstellen. Pakete sind 24 Stunden wieder aufnehmbar.</p></main></html>',{headers:{'Content-Type':'text/html; charset=utf-8','Cache-Control':'no-store'}});
  })());
 }
});
