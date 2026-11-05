// Real synthetic FastAPI/SQLite. Run with run.py; never a working medical database.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
(async()=>{
 const origin=process.env.WP_H_ORIGIN,n=Number(process.env.COUNT||100),out=process.env.OUT;
 assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);assert([100,1000,10000].includes(n));assert(out);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const password='Synthetic-WPH-Research-991!',results={count:n,realBackend:true,syntheticOnly:true,roles:[],checks:[],axe:[]};
 async function api(c,method,url,data){const csrf=(await c.cookies()).find(x=>x.name==='clinic_csrf')?.value;const r=await c.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),`${method} ${url}: ${r.status()}`);return r.json();}
 async function ready(p){await p.waitForFunction(()=>app.appReady&&!app.sessionExpired&&!app.sessionReconciling&&app.patientListState==='ready');}
 async function choose(p,id,value){const response=p.waitForResponse(r=>new URL(r.url()).pathname==='/api/patients'&&r.request().method()==='GET'&&r.status()===200);await p.locator('#'+id).selectOption(value);await response;await ready(p);}
 async function search(p,value){const response=p.waitForResponse(r=>{const u=new URL(r.url());return u.pathname==='/api/patients'&&(u.searchParams.get('q')||'')===value&&r.status()===200});await p.locator('#searchInput').fill(value);await response;await ready(p);}
 try{
  const admin=await browser.newContext();await api(admin,'POST','/api/auth/login',{username:'wph.dev',password});
  const roles=[['developer','wph.dev',n],['doctor','wph.doc0',Math.floor(n/5)],['department_head','wph.head',Math.floor(n*.8)]];
  for(const role of ['consilium_chair','deputy_chief_oncology','caop_head']){const username='wpi2.'+role;await api(admin,'POST','/api/users',{username,fullName:'Синтетический '+role,roleId:role,departmentIds:[],password,mustChangePassword:false});roles.push([role,username,role==='consilium_chair'?0:n]);}
  for(const [role,username,total] of roles){
   const c=await browser.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),p=await c.newPage();p.setDefaultTimeout(45000);
   // Isolate ordinary paging from unrelated audit/activity commits. No retries:
   // each request is forwarded once, in a deterministic order. Explicit
   // server release-race scenarios below still commit inside the page request.
   let apiQueue=Promise.resolve();
   await p.route('**/api/**',route=>{
    const job=apiQueue.then(async()=>{const response=await route.fetch();await route.fulfill({response});});
    apiQueue=job.catch(()=>{});return job;
   });
   let legacy=0,medicalWrites=0;const errors=[],pageRequests=[];
   p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{const u=new URL(r.url());if(u.pathname==='/api/patients'&&r.method()==='GET'){pageRequests.push(r.url());if(u.searchParams.get('view')!=='page')legacy++;}if(u.pathname.startsWith('/api/patients')&&!['GET','HEAD'].includes(r.method()))medicalWrites++;});
   await p.goto(origin);await p.locator('#username').fill(username);await p.locator('#password').fill(password);await p.locator('#loginSubmit').click();await ready(p);
   assert.equal(await p.locator('#patientsBody tr').count(),Math.min(50,total));assert.equal(await p.locator('#patientRegistryCount').textContent(),String(total));
   assert.deepEqual(await p.locator('#statsRow .stat__value').allTextContents(),[String(total),String(total),'0','0']);
   assert.equal(await p.locator('#filterDoctor,#filterPriority').count(),0);
   assert.equal(await p.locator('#patientsTable').getByRole('columnheader').count(),5);
   const rendered=await p.evaluate(()=>{const t=performance.now();app.renderPatients();return{ms:performance.now()-t,dom:document.querySelectorAll('*').length,rows:app.patientRows.length,details:app.patientDetails.size}});
   if(total>50){await p.getByRole('button',{name:'Следующая страница',exact:true}).click();await p.waitForFunction(()=>app.patientPage.page.offset===50&&app.patientListState==='ready');assert.equal(await p.locator('#patientsBody tr').count(),Math.min(50,total-50));results.checks.push('ordinary paging: exact offset50, deterministic single forwarding of API requests');await p.getByRole('button',{name:'Предыдущая страница',exact:true}).click();await p.waitForFunction(()=>app.patientPage.page.offset===0&&app.patientListState==='ready');}
   if(total){
    await choose(p,'sortOrderSelect','name');const target='Синтетический Пациент '+String(total-1).padStart(5,'0');if(total>50)assert.doesNotMatch(await p.locator('#patientsBody').textContent(),new RegExp(String(total-1).padStart(5,'0')));
    await search(p,target);assert.equal(await p.locator('#patientsBody tr').count(),1);assert.equal(await p.evaluate(()=>app.patientPage.page.total),1);assert.equal(await p.locator('#patientRegistryCount').textContent(),String(total));assert.equal(await p.locator('#statsRow .stat__value').first().textContent(),String(total));
    const id=await p.evaluate(()=>app.patientRows[0].id);await p.locator('.patient-open').click();await p.waitForFunction(()=>app.currentView==='patient'&&app.patientDetails.size===1);assert.equal(await p.evaluate(()=>app.getPatient(app.currentPatientId).id),id);await p.locator('#backToList').click();
    await search(p,'');await p.locator('#registryMoreFilters summary').click();await choose(p,'filterDeadline','no-date');assert.equal(await p.locator('#patientsBody tr').count(),0);assert.equal(await p.locator('#patientsEmpty').isVisible(),true);assert.equal(await p.locator('#patientRegistryCount').textContent(),String(total));
    await p.getByRole('button',{name:/Снять условие: Срок:/}).click();await p.waitForFunction(()=>app.patientPage.page.total===Number(document.querySelector('#patientRegistryCount').textContent)&&app.patientListState==='ready');
    for(const sort of ['updated-newest','attention','newest','oldest','first-visit-oldest','first-visit-newest','name'])await choose(p,'sortOrderSelect',sort);
    await choose(p,'filterStage','exam');await p.locator('#resetPatientFiltersBtn').click();await p.waitForFunction(()=>!app.stageFilter&&app.patientListState==='ready');assert.equal(await p.locator('#sortOrderSelect').inputValue(),'name');
   }
   await p.evaluate(fs.readFileSync(process.env.AXE_SOURCE||path.join(__dirname,'../wp_h/node_modules/axe-core/axe.min.js'),'utf8'));
   for(const theme of ['dark','light']){await p.evaluate(t=>app.applyTheme(t),theme);for(const width of [1440,375]){
    await p.setViewportSize({width,height:width===1440?1000:812});await p.screenshot({path:path.join(out,`${role}-${theme}-${width}.png`),animations:'disabled'});
    assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    if(width===375){assert.equal(await p.locator('.topbar__brand').isVisible(),false);await p.locator('#sidebarToggle').click();assert.equal(await p.locator('#sidebar').evaluate(e=>e.inert),false);await p.keyboard.press('Escape');await p.waitForFunction(()=>document.querySelector('#sidebar').inert);await p.locator('#openFiltersSheetBtn').click();for(const control of ['filterStage','filterHead','filterDeadline','filterAttention','filterAttentionReason','sortOrderSelect'])assert.equal(await p.locator('#filtersSheet #'+control).isVisible(),true,'mobile control '+control);await p.locator('#filterDeadline').focus();await p.keyboard.press('Escape');assert.equal(await p.locator('#filtersSheet').isVisible(),false);assert.equal(await p.evaluate(()=>document.activeElement.id),'openFiltersSheetBtn');}
    const a=await p.evaluate(async()=>{await Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})));const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(x=>({target:x.target,summary:x.failureSummary}))}))});results.axe.push({role,theme,width,violations:a});
   }}
   await p.setViewportSize({width:1440,height:1000});
   if(role==='developer'){
    // Hold an actual successful response to test loading and stale-response ownership.
    let release,seen;const held=new Promise(r=>{seen=r});
    await p.route('**/api/patients?*',async route=>{if(new URL(route.request().url()).searchParams.get('q')!=='Синтетический Пациент 00001')return route.continue();const response=await route.fetch();seen();await new Promise(r=>{release=r});await route.fulfill({response});});
    await p.evaluate(()=>{app.searchQuery='Синтетический Пациент 00001';window.i2Old=app.reloadPatientsAndRender()});await held;
    assert.equal(await p.locator('#view-patients').getAttribute('aria-busy'),'true');assert.equal(await p.locator('#patientRegistryResults').evaluate(e=>e.inert),true);
    await search(p,'Синтетический Пациент 00002');release();await p.evaluate(()=>window.i2Old);assert.match(await p.locator('#patientsBody').textContent(),/00002/);assert.doesNotMatch(await p.locator('#patientsBody').textContent(),/00001/);await p.unroute('**/api/patients?*');await search(p,'');
    // Real backend release race: never more than one automatic refresh.
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'once'});let before=pageRequests.length;await p.evaluate(()=>app.reloadPatientsAndRender());assert.equal(pageRequests.length-before,2);assert.equal(await p.evaluate(()=>app.patientListState),'ready');
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'always'});before=pageRequests.length;await p.evaluate(()=>app.reloadPatientsAndRender());assert.equal(pageRequests.length-before,2);assert.equal(await p.locator('#patientListError').isVisible(),true);assert.equal(await p.locator('#patientRegistryResults').isVisible(),false);assert.equal(await p.locator('#patientRegistryCount').textContent(),'—');assert.equal(await p.evaluate(()=>!!app.sessionExpired),false);
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'none'});await p.locator('#retryPatientListBtn').click();await ready(p);
    // Intentional technical commit after validation: no patient data changes.
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'technical-once'});before=pageRequests.length;
    await p.evaluate(()=>app.changePatientPage(1));assert.equal(pageRequests.length-before,2);await ready(p);
    assert.equal(await p.evaluate(()=>app.patientPage.page.offset),0);assert.equal(await p.evaluate(()=>app.pageIndex),0);
    assert.doesNotMatch(await p.locator('#patientsBody').textContent(),/00099/);
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'technical-always'});before=pageRequests.length;
    await p.evaluate(()=>app.changePatientPage(1));assert.equal(pageRequests.length-before,2);assert.equal(await p.locator('#patientListError').isVisible(),true);assert.equal(await p.locator('#patientRegistryResults').isVisible(),false);
    await api(admin,'POST','/synthetic/wph/release-race',{mode:'none'});await p.locator('#retryPatientListBtn').click();await ready(p);
    results.checks.push('intentional sessions commit: data_version409; exactly one restart to offset0; repeated409 stops; no stale second page');
    // Controlled transport faults are UI tests; real audit faults are covered by pytest.
    let fault=410;await p.route('**/api/patients?*',async route=>{if(fault){const status=fault;fault=0;return route.fulfill({status,contentType:'application/json',body:JSON.stringify({code:status===410?'CURSOR_EXPIRED':'AUDIT_UNAVAILABLE',detail:'Synthetic transport fault'})})}return route.continue()});
    before=pageRequests.length;await p.evaluate(()=>app.reloadPatientsAndRender());assert.equal(pageRequests.length-before,2);await ready(p);
    fault=503;before=pageRequests.length;await p.evaluate(()=>app.reloadPatientsAndRender());assert.equal(pageRequests.length-before,1);assert.equal(await p.locator('#patientRegistryResults').isVisible(),false);assert.equal(await p.locator('#patientListError').isVisible(),true);await p.unroute('**/api/patients?*');await p.locator('#retryPatientListBtn').click();await ready(p);
    results.checks.push('loading/inert; held stale response; real release409 once/persistent; transport410/503; explicit recovery');
   }
   assert.equal(legacy,0);assert.equal(medicalWrites,0);assert.deepEqual(errors,[]);
   const metrics=await (await c.request.get(origin+'/synthetic/wph/metrics')).json();assert(metrics.samples.filter(s=>s.status===200).every(s=>s.audit_hash_matches));
   results.roles.push({role,total,legacy,medicalWrites,rendered,requests:pageRequests.length,technicalCommits:metrics.samples.filter(s=>s.technical_commit).map(s=>({status:s.status,...s.technical_commit}))});await c.close();
  }
  // Malicious values pass through the real API, then render as inert text in the new row.
  const xss='Синтетический <img src=x onerror="window.i2Xss=1">';const card=await api(admin,'POST','/api/patients',{name:xss,diagnosis:'C34.1',priority:'normal',stage:'exam',head:'Синтетический Заведующий'});
  const c=await browser.newContext(),p=await c.newPage();await p.goto(origin);await p.locator('#username').fill('wph.dev');await p.locator('#password').fill(password);await p.locator('#loginSubmit').click();await ready(p);await search(p,'Синтетический <img');assert.equal(await p.locator('#patientsBody .patient-open').textContent(),xss);assert.equal(await p.locator('#patientsBody img').count(),0);assert.equal(await p.evaluate(()=>window.i2Xss),undefined);assert.equal(await p.evaluate(()=>app.patientRows[0].id),card.id);await c.close();await admin.close();
  // Identity/authorization failures never enter the benign list-restart branch.
  for(const status of [401,409]){
   const ctx=await browser.newContext(),pg=await ctx.newPage();await pg.goto(origin);await pg.locator('#username').fill('wph.dev');await pg.locator('#password').fill(password);await pg.locator('#loginSubmit').click();await ready(pg);let count=0;
   await pg.route('**/api/patients?*',route=>{count++;return route.fulfill({status,contentType:'application/json',body:JSON.stringify({code:status===409?'ACCESS_CONTEXT_CHANGED':'NOT_AUTHENTICATED'})});});
   await pg.evaluate(()=>app.reloadPatientsAndRender().catch(()=>{}));assert.equal(count,1);assert.equal(await pg.evaluate(()=>app.sessionExpired),true);await ctx.close();
  }
  results.checks.push('401 and ACCESS_CONTEXT_CHANGED: one request, locked session, no list retry');
  assert.deepEqual(results.axe.flatMap(x=>x.violations),[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify(results));
 }finally{await browser.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
