// Real v32 Clinic service, ephemeral SQLite and virtual authenticators only.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE),assert=require('node:assert/strict');
(async()=>{
 const origin=process.env.STAGE2C_SYNTHETIC_ORIGIN;assert.match(origin,/^http:\/\/localhost:\d+$/);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 try{
  const page=await browser.newPage();await page.goto(origin+'/synthetic/stage2c/config');
  const cdp=await page.context().newCDPSession(page);await cdp.send('WebAuthn.enable');
  async function call(path,body){return page.evaluate(async({path,body})=>{
   const r=await fetch('/synthetic/stage2c/'+path,{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body||{})});return {status:r.status,body:await r.json()};
  },{path,body});}
  async function credential(kind,key){return page.evaluate(async({kind,key})=>{
   const dec=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
   const enc=b=>b==null?null:btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
   key.challenge=dec(key.challenge);if(key.user)key.user.id=dec(key.user.id);
   for(const k of ['allowCredentials','excludeCredentials'])if(key[k])key[k]=key[k].map(c=>({...c,id:dec(c.id)}));
   const c=await navigator.credentials[kind]({publicKey:key}),r=c.response;
   return {id:c.id,rawId:enc(c.rawId),type:c.type,response:kind==='create'?{clientDataJSON:enc(r.clientDataJSON),attestationObject:enc(r.attestationObject)}:{clientDataJSON:enc(r.clientDataJSON),authenticatorData:enc(r.authenticatorData),signature:enc(r.signature),userHandle:enc(r.userHandle)}};
  },{kind,key});}
  let r=await call('start');assert.equal(r.status,200);const grant=r.body;
  const isolated=await browser.newContext();await isolated.addCookies([{name:'clinic_session',value:grant.grant,url:origin}]);
  for(const path of ['/api/patients','/api/users','/api/export'])assert.equal((await isolated.request.get(origin+path)).status(),401);
  const notices=[];
  for(let i=0;i<2;i++){
   const auth=(await cdp.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'usb',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}})).authenticatorId;
   r=await call('options',grant);assert.equal(r.status,200);const f=r.body;
   const response=await credential('create',f.options.publicKey);
   r=await call('finish',{...grant,flow:f.flow,response});assert.equal(r.status,200);notices.push(r.body.notice);const cid=r.body.credential;
   assert.equal((await call('finish',{...grant,flow:f.flow,response})).status,409);
   r=await call('options',{...grant,credential:cid});assert.equal(r.status,200);
   const assertion=await credential('get',r.body.options.publicKey);
   r=await call('finish',{...grant,flow:r.body.flow,response:assertion});assert.equal(r.status,200);assert.equal(r.body.possession,true);
   await cdp.send('WebAuthn.removeVirtualAuthenticator',{authenticatorId:auth});
  }
  assert.equal((await call('activate',grant)).status,403);
  for(const notice of notices)assert.equal((await call('confirm',{notice})).status,200);
  r=await call('activate',grant);assert.equal(r.status,200);assert.equal(r.body.medicalSession,false);assert.equal(r.body.reauthenticationRequired,true);
  assert.equal((await call('options',grant)).status,401);
  for(const path of ['/api/patients','/api/users','/api/export'])assert.equal((await isolated.request.get(origin+path)).status(),401);
  await isolated.close();
  console.log(JSON.stringify({real_backend:true,real_webauthn:true,independent_virtual_authenticators:2,physical_independence_qualified:false,limited_grant_isolation:true,challenge_replay_denied:true,activation_requires_notice:true,active_without_medical_session:true,used_grant_denied:true,browser:browser.version(),synthetic_only:true}));
 }finally{await browser.close();}
})().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});
