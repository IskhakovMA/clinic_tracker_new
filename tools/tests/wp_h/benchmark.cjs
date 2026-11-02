/* Synthetic-only before/after protocol. One sample per role/size, not an SLA. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),fs=require('node:fs'),assert=require('node:assert/strict');
(async()=>{
 const origin=process.env.WP_H_ORIGIN,n=Number(process.env.COUNT),out=process.env.OUT;
 assert(origin&&out&&[100,1000,10000].includes(n));
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const results={count:n,base:'c7389fde6fe1f6d83d68ed65b9db634be624ce13',browser:browser.version(),roles:[],syntheticOnly:true};
 try{
  for(const role of ['dev','doc0','head']){
   const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(180000);
   const cdp=await context.newCDPSession(page);await cdp.send('Performance.enable');
   const requests=[];page.on('request',r=>{if(new URL(r.url()).pathname==='/api/patients')requests.push(r.url());});
   await page.goto(origin);await page.locator('#username').fill('wph.'+role);await page.locator('#password').fill('Synthetic-WPH-Research-991!');
   const t=Date.now();await page.locator('#loginSubmit').click();
   await page.waitForFunction(()=>typeof app!=='undefined'&&app.patientRows.length>0&&document.querySelectorAll('#patientsBody tr').length>0);
   const expected=role==='dev'?n:role==='head'?Math.floor(n*.8):Math.floor(n/5);
   assert.equal(await page.evaluate(()=>app.patientPage.page.total),expected);
   assert.equal(await page.evaluate(()=>app.patientRows.length),Math.min(50,expected));
   const initialMs=Date.now()-t;
   const rendering=await page.evaluate(async()=>{const start=performance.now();app.renderPatients();const syncMs=performance.now()-start;await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));return{syncMs,throughPaintMs:performance.now()-start,dom:document.querySelectorAll('*').length,rows:document.querySelectorAll('#patientsBody tr').length};});
   const perf=(await cdp.send('Performance.getMetrics')).metrics;const get=name=>perf.find(x=>x.name===name)?.value;
   const metrics=await (await context.request.get(origin+'/synthetic/wph/metrics')).json();
   const traversal=await page.evaluate(async()=>{const seen=new Set(app.patientRows.map(p=>p.id)),pages=[];for(let i=0;i<3&&app.patientPage.page.hasNext;i++){
    const start=performance.now();await app.changePatientPage(1);await new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)));
    for(const p of app.patientRows){if(seen.has(p.id))throw new Error('Duplicate across pages');seen.add(p.id);}
    pages.push({offset:app.patientPage.page.offset,rows:app.patientRows.length,detailCache:app.patientDetails.size,dom:document.querySelectorAll('*').length,ms:performance.now()-start});
   }return{pages,unique:seen.size};});
   const endMetrics=await (await context.request.get(origin+'/synthetic/wph/metrics')).json();
   assert(requests.every(u=>new URL(u).searchParams.get('view')==='page'));
   assert(traversal.pages.every(p=>p.rows<=50&&p.detailCache===0));
   assert(metrics.samples.at(-1).audit_hash_matches);
   assert(endMetrics.samples.slice(metrics.samples.length).every(s=>s.audit_hash_matches));
   results.roles.push({role,visible:expected,initialMs,rendering,JSHeapUsedSize:get('JSHeapUsedSize'),CDPNodes:get('Nodes'),api:metrics.samples.at(-1),audit:metrics.audit,traversal,traversalApi:endMetrics.samples.slice(metrics.samples.length),heapAfterPages:(await cdp.send('Performance.getMetrics')).metrics.find(x=>x.name==='JSHeapUsedSize').value});
   await context.close();fs.writeFileSync(out+'/after-'+n+'.json',JSON.stringify(results,null,2));
  }
 }finally{await browser.close();}
 console.log(JSON.stringify({count:n,roles:results.roles.map(r=>({role:r.role,visible:r.visible,apiMs:r.api.seconds*1000,select:r.api.select,bytes:r.api.bytes,renderMs:r.rendering.syncMs,dom:r.rendering.dom}))}));
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
