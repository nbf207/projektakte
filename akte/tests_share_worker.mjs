// Synthetic Service Worker protocol test in Node; no browser or screenshots.
import fs from 'node:fs';
import vm from 'node:vm';
import assert from 'node:assert/strict';
import {randomUUID} from 'node:crypto';
const folder=process.argv[2];
for(const name of ['share.js','pwa.js','voice.js'])new vm.Script(fs.readFileSync(folder+'/'+name,'utf8'));
const handlers={},entries=new Map();let networkCalls=0;
const cache={keys:async()=>[...entries.keys()].map(key=>new Request(key)),
 match:async request=>entries.get(typeof request==='string'?request:request.url)?.clone(),
 put:async(request,response)=>{entries.set(typeof request==='string'?request:request.url,response.clone());},
 delete:async request=>entries.delete(typeof request==='string'?request:request.url)};
const context={self:{location:{origin:'https://projektakte.test'},addEventListener:(name,callback)=>handlers[name]=callback,
 skipWaiting:async()=>{},clients:{claim:async()=>{}}},caches:{open:async()=>cache},crypto:{randomUUID},
 Request,Response,FormData,URL,Date,fetch:async()=>{networkCalls++;throw Error('offline');}};
vm.runInNewContext(fs.readFileSync(folder+'/auth.js','utf8'),context);
vm.runInNewContext(fs.readFileSync(folder+'/sw.js','utf8'),context);
async function share(files,text='Synthetic note'){
 const data=new FormData();data.set('text',text);for(const file of files)data.append('files',file,file.name);
 let result;handlers.fetch({request:new Request('https://projektakte.test/teilen/',{method:'POST',body:data}),respondWith:promise=>result=promise});return result;
}
const photo=new File(['synthetic jpeg'],'SYN.jpg',{type:'image/jpeg'}),pdf=new File(['%PDF-synthetic'],'SYN.pdf',{type:'application/pdf'});
const responses=await Promise.all([share([photo,pdf]),share([new File(['image'],'empty-mime.png')])]);
assert.equal(responses[0].status,303);assert.equal(responses[1].status,303);assert.equal(networkCalls,0);
const first=new URL(responses[0].headers.get('Location')),second=new URL(responses[1].headers.get('Location'));
assert.equal(first.pathname,'/teilen/');assert.notEqual(first.searchParams.get('paket'),second.searchParams.get('paket'));
assert.ok(first.searchParams.get('paket'));assert.ok(second.searchParams.get('paket'));
const key='https://projektakte.test/__share-cache__/'+first.searchParams.get('paket');
const payload=await (await cache.match(key)).formData();
assert.deepEqual(payload.getAll('files').map(file=>file.name),['SYN.jpg','SYN.pdf']);assert.equal(payload.get('text'),'Synthetic note');
assert.equal((await payload.getAll('files')[1].text()),'%PDF-synthetic');
const before=entries.size;
const invalid=await share([new File(['bad'],'bad.txt',{type:'text/plain'})]);assert.equal(new URL(invalid.headers.get('Location')).searchParams.get('fehler'),'format');assert.equal(entries.size,before);
const large=await share(Array(9).fill(photo));assert.equal(new URL(large.headers.get('Location')).searchParams.get('fehler'),'dateien');
// Normal Django upload must bypass the worker entirely and keep CSRF enforcement.
let intercepted=false;handlers.fetch({request:new Request('https://projektakte.test/teilen/speichern/',{method:'POST'}),respondWith:()=>intercepted=true});assert.equal(intercepted,false);
handlers.fetch({request:new Request('https://projektakte.test/projekte/'),respondWith:()=>intercepted=true});assert.equal(intercepted,false);
let offline;handlers.fetch({request:new Request(first),respondWith:promise=>offline=promise});assert.match(await (await offline).text(),/Dateien bleiben im Browser/);assert.ok(await cache.match(key));
const expired='https://projektakte.test/__share-cache__/old';await cache.put(expired,new Response('expired',{headers:{'X-Share-Created':String(Date.now()-25*60*60*1000)}}));await share([pdf]);assert.equal(await cache.match(expired),undefined);
await cache.delete(key);assert.equal(await cache.match(key),undefined);assert.ok(await cache.match('https://projektakte.test/__share-cache__/'+second.searchParams.get('paket')));
console.log('Worker protocol, isolated batches, multipart recovery, expiry, offline retention and CSRF boundary: OK');

// An expired Cloudflare Access session must preserve queued files and offer
// top-level authentication, without following a POST to the identity provider.
context.fetch=async(request,options)=>{
 assert.equal(options.credentials,'same-origin');assert.equal(options.redirect,'manual');
 return Response.redirect('https://test-team.cloudflareaccess.com/cdn-cgi/access/login/app',302);
};
let expiredAuth;const pendingBefore=entries.size;
handlers.fetch({request:new Request(second),respondWith:promise=>expiredAuth=promise});
const page=await(await expiredAuth).text();assert.match(page,/Anmeldung erneuern/);assert.match(page,/auth_refresh=1/);assert.equal(entries.size,pendingBefore);
let bypass=false;const refresh=new URL(second);refresh.searchParams.set('auth_refresh','1');
handlers.fetch({request:new Request(refresh),respondWith:()=>bypass=true});assert.equal(bypass,false);
assert.equal(context.ProjektakteNet.providerURL('https://accounts.google.com/login'),true);
assert.equal(context.ProjektakteNet.providerURL('https://evilcloudflareaccess.com/'),false);
await assert.rejects(context.ProjektakteNet.requestJSON('/teilen/speichern/',{method:'POST'}),error=>error.name==='LoginRequired');
console.log('Cloudflare/Google redirect detection and cached-file retention: OK');
