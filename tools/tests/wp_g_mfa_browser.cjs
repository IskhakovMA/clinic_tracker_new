// Real Clinic API/UI, synthetic temporary database and virtual authenticators only.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const assert=require('node:assert/strict');
(async()=>{
 const origin=process.env.SYNTHETIC_MFA_ORIGIN;assert.match(origin,/^http:\/\/localhost:\d+$/);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 try {
  const page=await browser.newPage();await page.goto(origin+'/synthetic/mfa/config');
  const cdp=await page.context().newCDPSession(page);await cdp.send('WebAuthn.enable');
  const auth=(await cdp.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'usb',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}})).authenticatorId;
  const config=await page.evaluate(()=>fetch('/synthetic/mfa/config').then(r=>r.json()));
  async function credential(kind,key){return page.evaluate(async({kind,key})=>{
   const dec=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
   const enc=b=>b==null?null:btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
   key={...key,challenge:dec(key.challenge)};
   if(key.user)key.user={...key.user,id:dec(key.user.id)};
   for(const n of ['allowCredentials','excludeCredentials'])if(key[n])key[n]=key[n].map(c=>({...c,id:dec(c.id)}));
   const c=await navigator.credentials[kind]({publicKey:key});const r=c.response;
   return {id:c.id,rawId:enc(c.rawId),type:c.type,clientExtensionResults:c.getClientExtensionResults(),response:kind==='create'?{clientDataJSON:enc(r.clientDataJSON),attestationObject:enc(r.attestationObject)}:{clientDataJSON:enc(r.clientDataJSON),authenticatorData:enc(r.authenticatorData),signature:enc(r.signature),userHandle:enc(r.userHandle)}};
  },{kind,key});}
  async function call(path,body,registration=false){return page.evaluate(async({path,body,registration})=>{
   const cookie=name=>document.cookie.split('; ').find(p=>p.startsWith(name+'='))?.split('=').slice(1).join('=');
   const csrf=cookie(path==='/api/auth/mfa/authenticate'?'clinic_pre_auth_csrf':'clinic_csrf');
   const r=await fetch(path,{method:body?'POST':'GET',headers:{'Content-Type':'application/json',...(csrf?{'X-CSRF-Token':csrf}:{}),...(registration?{'X-MFA-CSRF-Token':cookie('clinic_pre_auth_csrf')}:{})},body:body?JSON.stringify(body):undefined});return {status:r.status,body:await r.json()};
  },{path,body,registration});}
  const pairs=[];
  for(let i=0;i<2;i++){
   const registration=await credential('create',config.options.publicKey);
   const assertion=await credential('get',{challenge:config.proof,rpId:'localhost',userVerification:'required',allowCredentials:[{id:registration.id,type:'public-key'}]});pairs.push({registration,assertion});
  }
  assert.equal((await call('/synthetic/mfa/setup',{pairs})).status,200);
  assert.equal((await call('/api/auth/login',{username:'synthetic.switch.a',password:'synthetic-browser-password'})).status,200);
  const captured=await browser.newContext();
  await captured.addCookies((await page.context().cookies()).filter(c=>c.name==='clinic_session'));
  assert.equal((await captured.request.get(origin+'/api/patients')).status(),200);
  const independent=await browser.newContext();
  assert.equal((await independent.request.post(origin+'/api/auth/login',{data:{username:'synthetic.switch.a',password:'synthetic-browser-password'}})).status(),200);
  let r=await call('/api/auth/login',{username:'synthetic.mfa',password:'synthetic-browser-password'});assert.equal(r.body.stage,'webauthn');
  assert.equal((await captured.request.get(origin+'/api/patients')).status(),401);
  assert.equal((await independent.request.get(origin+'/api/patients')).status(),200);
  for(const path of ['/api/patients','/api/users','/api/export'])assert.equal((await call(path)).status,401);
  // Exercise the real frontend login flow; no programmatic session injection.
  await page.goto(origin);
  await page.locator('#username').fill('synthetic.mfa');await page.locator('#password').fill('synthetic-browser-password');await page.locator('#loginSubmit').click();
  await page.waitForFunction(()=>typeof app!=='undefined' && app.user?.username==='synthetic.mfa' && !app.sessionExpired);
  assert.equal((await call('/api/patients')).status,200);
  // Isolate deliberate cookie tampering from the real UI's WP-D reload guard.
  await page.goto(origin+'/synthetic/mfa/config');
  const opts=await call('/api/auth/mfa/register/options',{});assert.equal(opts.status,200);
  await cdp.send('WebAuthn.setAutomaticPresenceSimulation',{authenticatorId:auth,enabled:false});
  await cdp.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'usb',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}});
  const pending=await credential('create',opts.body.publicKey);
  const parentCookies=(await page.context().cookies()).filter(c=>['clinic_session','clinic_csrf'].includes(c.name));
  await page.context().addCookies((await independent.cookies()).filter(c=>['clinic_session','clinic_csrf'].includes(c.name)));
  assert.equal((await call('/api/auth/mfa/register',pending,true)).status,403);
  // The unauthorized actor did not consume the rightful owner's flow.
  await page.context().addCookies(parentCookies);
  r=await call('/api/auth/mfa/register',pending,true);assert.equal(r.status,200);assert.equal(r.body.state,'PENDING');
  const pwd=await call('/api/auth/change-password',{currentPassword:'synthetic-browser-password',newPassword:'Synthetic-updated-password-975'});assert.equal(pwd.status,200);assert.equal(pwd.body.reauthenticationRequired,true);
  assert.equal((await call('/api/patients')).status,401);
  console.log(JSON.stringify({real_clinic_backend:true,real_frontend_login:true,captured_session_revoked:true,independent_device_retained:true,mixed_registration_rejected:true,virtual_authenticators:true,preauth_isolation:true,pending_registration_no_activation:true,password_change_no_bypass:true,browser:browser.version(),physical_independence_tested:false}));
 } finally {await browser.close();}
})().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});
