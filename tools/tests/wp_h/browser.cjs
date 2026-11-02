// Real isolated FastAPI and synthetic accounts only. No S1 bypass.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright');
const fs=require('node:fs'),assert=require('node:assert/strict'),path=require('node:path');
(async()=>{
 const origin=process.env.WP_H_ORIGIN;assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const results={syntheticOnly:true,realBackend:true,checks:[],axe:[]};
 try{
  const context=await browser.newContext({viewport:{width:1440,height:1000}}),page=await context.newPage();page.setDefaultTimeout(30000);
  let legacy=0;page.on('request',r=>{const u=new URL(r.url());if(u.pathname==='/api/patients'&&r.method()==='GET'&&u.searchParams.get('view')!=='page')legacy++;});
  const errors=[];page.on('pageerror',e=>errors.push(e.message));
  async function settled(){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.waitForFunction(()=>app.appReady&&!app.sessionExpired&&!app.sessionReconciling);}
  async function login(p,user){await p.goto(origin);await p.bringToFront();await p.locator('#username').fill(user);await p.locator('#password').fill('Synthetic-WPH-Research-991!');await p.locator('#loginSubmit').click();await p.waitForFunction(()=>app.appReady&&!app.sessionExpired);}
  async function api(ctx,method,url,data){const csrf=(await ctx.cookies()).find(c=>c.name==='clinic_csrf')?.value;const r=await ctx.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),`${method} ${url}: ${r.status()}`);return r.json();}
  await login(page,'wph.dev');await settled();
  assert.equal(await page.locator('#patientsBody tr').count(),50);assert.equal(await page.evaluate(()=>app.patientPage.page.total),100);
  await page.getByRole('button',{name:'Следующая страница',exact:true}).click();await page.waitForFunction(()=>app.patientPage.page.offset===50);assert.equal(await page.locator('#patientsBody tr').count(),50);
  await page.locator('#searchInput').fill('Синтетический Пациент 00099');await page.waitForFunction(()=>app.patientPage.page.total===1);assert.match(await page.locator('#patientsBody').textContent(),/00099/);
  results.checks.push('50-row pages; full-scope out-of-page search; server totals');
  let releaseOld,seenOld;
  const oldSeen=new Promise(r=>{seenOld=r;});
  await page.route('**/api/patients?*',async route=>{
   if(new URL(route.request().url()).searchParams.get('q')!=='Синтетический Пациент 00001')return route.continue();
   const response=await route.fetch();seenOld();await new Promise(r=>{releaseOld=r;});await route.fulfill({response});
  });
  await page.evaluate(()=>{app.searchQuery='Синтетический Пациент 00001';window.wphOld=app.reloadPatientsAndRender();});await oldSeen;
  await page.locator('#searchInput').fill('Синтетический Пациент 00002');await page.waitForFunction(()=>app.patientRows.length===1&&app.patientRows[0].name.endsWith('00002'));
  releaseOld();await page.evaluate(()=>window.wphOld);assert.match(await page.locator('#patientsBody').textContent(),/00002/);assert.doesNotMatch(await page.locator('#patientsBody').textContent(),/00001/);
  await page.unroute('**/api/patients?*');results.checks.push('held stale page response ignored after newer search');

  await page.locator('#searchInput').fill('');await page.waitForFunction(()=>app.patientPage.page.total===100);
  const selected=await page.evaluate(()=>app.patientRows[0].id);await page.evaluate(id=>app.openPatient(id),selected);
  await page.getByRole('tab',{name:'Данные',exact:true}).click();assert.equal(await page.evaluate(()=>document.activeElement.id),'tab-data');
  await page.keyboard.press('ArrowRight');assert.equal(await page.evaluate(()=>document.activeElement.id),'tab-complaints');
  await page.getByRole('tab',{name:'Данные',exact:true}).click();
  const head=await page.locator('#patientPanels .field').filter({has:page.locator('.field__label',{hasText:/^Заведующий$/})}).locator('select').inputValue();assert.equal(head,'Синтетический Заведующий');
  const axePath=process.env.AXE_SOURCE||path.join(__dirname,'node_modules/axe-core/axe.min.js');
  await page.evaluate(fs.readFileSync(axePath,'utf8'));
  for(const theme of ['dark','light']){
   await page.evaluate(t=>app.applyTheme(t),theme);await settled();
   await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSTransition).map(a=>a.finished.catch(()=>{})));});
   const a=await page.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}));});
   results.axe.push({view:'card-data',theme,violations:a});
  }
  results.checks.push('Russian server heads; labeled controls; tabs keyboard/focus');
  await page.getByRole('tab',{name:'Обзор',exact:true}).click();const trigger=page.locator('#patientPanels').getByRole('button',{name:'Добавить проблему',exact:true});await trigger.click();
  const modal=page.locator('#attentionModal');await page.keyboard.press('Shift+Tab');assert(await modal.evaluate(e=>e.contains(document.activeElement)));await page.keyboard.press('Tab');assert(await modal.evaluate(e=>e.contains(document.activeElement)));await page.keyboard.press('Escape');await settled();assert(await trigger.evaluate(e=>e===document.activeElement));
  results.checks.push('modal focus trap; Escape; id-less trigger return');
  for(const view of ['patient','patients']){
   await page.evaluate(v=>app.showView(v),view);
   for(const theme of ['dark','light']){
    await page.evaluate(t=>app.applyTheme(t),theme);await settled();
    await page.evaluate(async()=>{await Promise.all(document.getAnimations().filter(a=>a instanceof CSSTransition).map(a=>a.finished.catch(()=>{})));});
    const violations=await page.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}));});
    results.axe.push({view:view==='patient'?'card-overview':'list',theme,violations});
   }
  }
  await page.evaluate(()=>app.showView('patients'));await page.setViewportSize({width:375,height:812});await settled();
  assert.equal(await page.locator('#sidebar').evaluate(e=>e.inert),true);
  await page.locator('#sidebarToggle').click();assert.equal(await page.locator('#sidebarToggle').getAttribute('aria-expanded'),'true');assert.equal(await page.locator('#sidebar').evaluate(e=>e.inert),false);
  await page.keyboard.press('Escape');await page.waitForFunction(()=>document.querySelector('#sidebar').inert);
  for(let i=0;i<20;i++){await page.keyboard.press('Tab');assert.equal(await page.evaluate(()=>!!document.activeElement.closest('#sidebar')),false);}
  assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);results.checks.push('375px inert menu, aria-expanded, keyboard; no horizontal overflow');
  await page.setViewportSize({width:1440,height:1000});await settled();
  // A genuine concurrent insert makes the next cursor stale. No retry loop.
  await api(context,'POST','/api/patients',{name:'Синтетический Конкурентный',diagnosis:'C34.1',priority:'normal',stage:'exam',head:'Синтетический Заведующий'});
  await page.getByRole('button',{name:'Следующая страница',exact:true}).click();await page.waitForFunction(()=>app.patientPage.page.total===101&&app.patientPage.page.offset===0);
  assert.equal(await page.evaluate(()=>app.currentPatientId),selected);assert(await page.evaluate(id=>!!app.getPatient(id),selected));results.checks.push('stale revision409: single refresh, selected detail retained');
  await page.evaluate(()=>app.openExportModal());await page.waitForFunction(()=>app.exportPage?.page.total===101);await page.locator('#exportSelectAllBtn').click();assert.equal(await page.locator('#exportSelectedCount').textContent(),'101');
  const selection=await page.evaluate(()=>app.exportAllSelection);const response=await api(context,'POST','/api/export/selection',{query:selection.query,revision:selection.revision});assert.equal(response.patients.length,101);assert(response.signature);await page.locator('#closeExportIconBtn').click();results.checks.push('full filtered signed export, not current50');
  await page.evaluate(()=>app.showView('calendar'));await page.waitForFunction(()=>document.querySelector('#calendarPanel .pagination'));assert.equal(await page.locator('#calendarPanel .list-row').count(),50);
  await page.evaluate(()=>app.showView('checklists'));await page.waitForFunction(()=>document.querySelector('#checklistsPanel .pagination'));results.checks.push('independent bounded calendar/checklists');
  assert.equal(legacy,0);assert.deepEqual(errors,[]);results.checks.push('no hidden legacy list downloads; no page errors');
  for(const role of ['doc0','head']){const c=await browser.newContext(),p=await c.newPage();await login(p,'wph.'+role);const data=await p.evaluate(()=>({total:app.patientPage.summary.total,rows:app.patientRows.length}));assert.equal(data.total,role==='doc0'?20:80);assert.equal(data.rows,role==='doc0'?20:50);await c.close();}results.checks.push('doctor/head scoped totals and pages');
  const violations=results.axe.flatMap(a=>a.violations);console.log(JSON.stringify(results));assert.deepEqual(violations,[],'axe WCAG A/AA violations');
  await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
