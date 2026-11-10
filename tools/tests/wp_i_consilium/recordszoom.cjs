// Native Chrome tab zoom in a throwaway profile; never a real browser profile.
// https://developer.chrome.com/docs/extensions/reference/api/tabs#method-setZoom
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),os=require('node:os'),path=require('node:path'),assert=require('node:assert/strict');
(async()=>{
 const origin=process.env.WP_H_ORIGIN;assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
 const dir=fs.mkdtempSync(path.join(os.tmpdir(),'clinic-wph-native-zoom-')),ext=path.join(dir,'extension');fs.mkdirSync(ext);
 fs.writeFileSync(path.join(ext,'manifest.json'),JSON.stringify({manifest_version:3,name:'Synthetic localhost zoom verification',version:'1.0',permissions:['tabs'],background:{service_worker:'worker.js'}}));
 fs.writeFileSync(path.join(ext,'worker.js'),'chrome.runtime.onInstalled.addListener(()=>{});');
 let context;
 try{
  context=await chromium.launchPersistentContext(path.join(dir,'profile'),{headless:true,executablePath:process.env.WP_H_FULL_CHROMIUM||chromium.executablePath(),viewport:null,ignoreDefaultArgs:['--disable-extensions'],args:['--window-size=1280,1000','--disable-extensions-except='+ext,'--load-extension='+ext]});
  const page=await context.newPage();await page.goto(origin);await page.bringToFront();await page.locator('#username').fill('wph.dev');await page.locator('#password').fill('Synthetic-WPH-Research-991!');await page.locator('#loginSubmit').click();await page.waitForFunction(()=>app.appReady&&!app.sessionExpired);
  async function api(method,url,data) { const csrf=(await context.cookies()).find(x=>x.name==='clinic_csrf')?.value;const r=await context.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),url+' '+r.status());return r.json(); }
  const patient=(await api('GET','/api/patients?view=page&limit=1')).patients[0];
  await api('POST','/api/users',{username:'i5b.zoomchair',fullName:'Синтетический председатель zoom',roleId:'consilium_chair',departmentIds:[],password:'Synthetic-WPH-Research-991!',mustChangePassword:false});
  await api('POST','/api/consilium/schedule/generate',{scheduledDate:'2090-11-12',consiliumGroup:'thoracic',startTime:'10:00',endTime:'12:00',intervalMinutes:30});
  const record=await api('POST','/api/consilium/appointments',{patientId:patient.id,scheduledDate:'2090-11-12',scheduledTime:'10:00',consiliumGroup:'thoracic',hasBenefit:false,diagnosisLocalization:'Органы дыхания и грудной клетки',primaryDiagnosis:'C34.1',additionalDiagnosis:'',comment:'Синтетический zoom',additionalParticipant:'',needsSurgeon:false,needsRadiotherapist:false,uvRuv:false});
  await api('POST','/api/auth/logout',{});await api('POST','/api/auth/login',{username:'i5b.zoomchair',password:'Synthetic-WPH-Research-991!'});await api('POST','/api/consilium/appointments/'+record.id+'/review',{});await api('POST','/api/consilium/appointments/'+record.id+'/sign',{treatmentType:'chemotherapy',treatmentScheme:'Synthetic zoom scheme',goc:true,goc65Plus:true});await page.reload();await page.waitForFunction(()=>app.appReady&&!app.sessionExpired);
  await page.evaluate(async id=>{app.consilium.dateFilter='2090-11-12';app.showView('consilium');await app.renderConsilium();await app.openConsiliumArchive(true)},record.id);
  const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');const samples=[];
  for(const factor of [1,2,4]){
   const zoom=await worker.evaluate(async({origin,factor})=>{const tabs=await chrome.tabs.query({});const tab=tabs.find(t=>t.url.startsWith(origin+'/'));if(!tab)throw new Error('synthetic tab missing');await chrome.tabs.setZoom(tab.id,factor);return chrome.tabs.getZoom(tab.id);},{origin,factor});assert.equal(Math.round(zoom*100),factor*100);
   await page.waitForFunction(f=>Math.abs(devicePixelRatio-f)<0.01,factor);
   await page.waitForFunction(()=>app.appReady&&!app.sessionExpired&&!app.sessionReconciling);
   const data=await page.evaluate(()=>({width:innerWidth,outer:outerWidth,dpr:devicePixelRatio,overflow:document.documentElement.scrollWidth>innerWidth,rows:document.querySelectorAll('#patientsBody tr').length,scale:visualViewport.scale}));
   assert.equal(Math.round(data.dpr*100),factor*100);assert.equal(await page.evaluate(()=>app.currentView),'consilium');assert.equal(await page.locator('#consiliumArchiveModal').isVisible(),true);assert.equal(await page.locator('#consiliumArchiveBody button').count(),1);assert.equal(data.overflow,false);assert.equal(data.scale,1);
   if(factor===4)assert.equal(data.width,320);
   samples.push({nativeZoom:zoom,...data});
   if(process.env.OUT)await page.screenshot({path:path.join(process.env.OUT,`records-zoom-${factor*100}.png`)});
  }
  console.log(JSON.stringify({nativeChromeZoom:true,temporaryProfile:true,samples}));
 }finally{if(context)await context.close();fs.rmSync(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
