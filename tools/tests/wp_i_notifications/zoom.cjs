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
  context=await chromium.launchPersistentContext(path.join(dir,'profile'),{headless:true,reducedMotion:'reduce',executablePath:process.env.WP_H_FULL_CHROMIUM||chromium.executablePath(),viewport:null,ignoreDefaultArgs:['--disable-extensions'],args:['--window-size=1280,1000','--disable-extensions-except='+ext,'--load-extension='+ext]});
  const page=await context.newPage();await page.goto(origin);await page.bringToFront();await page.locator('#username').fill('wph.dev');await page.locator('#password').fill('Synthetic-WPH-Research-991!');await page.locator('#loginSubmit').click();await page.waitForFunction(()=>app.appReady&&!app.sessionExpired);
  async function api(method,url,data) { const csrf=(await context.cookies()).find(x=>x.name==='clinic_csrf')?.value;const r=await context.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),url+' '+r.status());return r.json(); }
  await page.evaluate(async()=>{app.showView('notifications');await app.renderInternalNotificationCenter()});
  await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
  const worker=context.serviceWorkers()[0]||await context.waitForEvent('serviceworker');const samples=[];
  for(const theme of ['light','dark'])for(const factor of [1,2,4]){
   await page.evaluate(t=>app.applyTheme(t),theme);
   await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));
   const zoom=await worker.evaluate(async({origin,factor})=>{const tabs=await chrome.tabs.query({});const tab=tabs.find(t=>t.url.startsWith(origin+'/'));if(!tab)throw new Error('synthetic tab missing');await chrome.tabs.setZoom(tab.id,factor);return chrome.tabs.getZoom(tab.id);},{origin,factor});assert.equal(Math.round(zoom*100),factor*100);
   await page.waitForFunction(f=>Math.abs(devicePixelRatio-f)<0.01,factor);
   await page.waitForFunction(()=>app.appReady&&!app.sessionExpired&&!app.sessionReconciling);
   const data=await page.evaluate(()=>({width:innerWidth,outer:outerWidth,dpr:devicePixelRatio,overflow:document.documentElement.scrollWidth>innerWidth,rows:document.querySelectorAll('#patientsBody tr').length,scale:visualViewport.scale}));
   assert.equal(Math.round(data.dpr*100),factor*100);assert.equal(await page.evaluate(()=>app.currentView),'notifications');assert.equal(await page.evaluate(()=>app.internalReady.full),true);assert.equal(data.overflow,false);assert.equal(data.scale,1);
   if(factor===4)assert.equal(data.width,320);
   const violations=await page.evaluate(async()=>{const a=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return a.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});if(violations.length){fs.writeFileSync(path.join(process.env.OUT,'axe-failure.json'),JSON.stringify({theme,factor,violations},null,2));await page.screenshot({path:path.join(process.env.OUT,'axe-failure.png')});}assert.deepEqual(violations,[]);
   samples.push({theme,nativeZoom:zoom,...data,violations});
   if(process.env.OUT)await page.screenshot({path:path.join(process.env.OUT,`notifications-${theme}-zoom-${factor*100}.png`)});
  }
  const result={nativeChromeZoom:true,temporaryProfile:true,samples};fs.writeFileSync(path.join(process.env.OUT,'results.json'),JSON.stringify(result,null,2));console.log(JSON.stringify(result));
 }finally{if(context)await context.close();fs.rmSync(dir,{recursive:true,force:true});}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
