// Synthetic public WebAuthn corpus. Private keys never leave Chromium memory.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE);
const http=require('node:http'),crypto=require('node:crypto'),fs=require('node:fs');
(async()=>{
 const server=http.createServer((req,res)=>res.end('<!doctype html><title>Synthetic Stage2C</title>'));
 await new Promise(r=>server.listen(0,'127.0.0.1',r));
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 try{
  const origin='http://localhost:'+server.address().port,page=await browser.newPage();await page.goto(origin);
  const cdp=await page.context().newCDPSession(page);await cdp.send('WebAuthn.enable');
  const challenge=crypto.randomBytes(32).toString('base64url'),handle=crypto.randomBytes(32).toString('base64url');
  const perform=(kind,id)=>page.evaluate(async({kind,id,challenge,handle})=>{
   const dec=s=>Uint8Array.from(atob(s.replace(/-/g,'+').replace(/_/g,'/')),c=>c.charCodeAt(0));
   const enc=b=>b==null?null:btoa(String.fromCharCode(...new Uint8Array(b))).replace(/\+/g,'-').replace(/\//g,'_').replace(/=+$/,'');
   const opts=kind==='create'?{challenge:dec(challenge),rp:{id:'localhost',name:'Synthetic'},user:{id:dec(handle),name:'synthetic',displayName:'Synthetic'},pubKeyCredParams:[{type:'public-key',alg:-7}],authenticatorSelection:{userVerification:'required',residentKey:'required'},attestation:'none'}:{challenge:dec(challenge),rpId:'localhost',allowCredentials:[{id:dec(id),type:'public-key'}],userVerification:'required'};
   const c=await navigator.credentials[kind]({publicKey:opts}),r=c.response;
   return {id:c.id,rawId:enc(c.rawId),type:c.type,response:kind==='create'?{clientDataJSON:enc(r.clientDataJSON),attestationObject:enc(r.attestationObject)}:{clientDataJSON:enc(r.clientDataJSON),authenticatorData:Array.from(new Uint8Array(r.authenticatorData)),signature:enc(r.signature),userHandle:enc(r.userHandle)}};
  },{kind,id,challenge,handle});
  const factors=[];
  for(let i=0;i<4;i++){
   const auth=(await cdp.send('WebAuthn.addVirtualAuthenticator',{options:{protocol:'ctap2',transport:'usb',hasResidentKey:true,hasUserVerification:true,isUserVerified:true,automaticPresenceSimulation:true}})).authenticatorId;
   const registration=await perform('create'),assertions=[];
   for(let n=0;n<5;n++)assertions.push(await perform('get',registration.id));
   factors.push({registration,assertions});await cdp.send('WebAuthn.removeVirtualAuthenticator',{authenticatorId:auth});
  }
  fs.writeFileSync('backend/tests/fixtures/mfa_stage2c_public_vectors.json',JSON.stringify({origin,challenge,handle,factors,synthetic_only:true,browser:browser.version(),private_keys_exported:false},null,2));
  console.log(JSON.stringify({factors:4,assertions:20,private_keys_exported:false,browser:browser.version()}));
 }finally{await browser.close();await new Promise(r=>server.close(r));}
})().catch(e=>{console.error(e.name+': '+e.message);process.exitCode=1;});
