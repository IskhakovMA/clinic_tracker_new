const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path');
const {setup,api,password}=require('./fixtures.cjs');
(async()=>{const origin=process.env.WP_H_ORIGIN,out=process.env.OUT;assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);assert(out);const b=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});const results={realBackend:true,syntheticOnly:true,checks:[],axe:[],metrics:[]};
const tabs={overview:'Обзор',notifications:'Уведомления',data:'Данные',complaints:'Комментарии',events:'События и даты',checklist:'Чек-лист',notes:'Заметки',history:'История'};
async function login(p,u){await p.goto(origin);await p.locator('#username').fill(u);await p.locator('#password').fill(password);await p.locator('#loginSubmit').click();await p.waitForFunction(()=>app.appReady&&!app.sessionExpired);}
async function tab(p,t){await p.getByRole('tab',{name:tabs[t],exact:true}).click();assert.equal(await p.evaluate(()=>app.currentTab),t);}
try{const f=await setup(b,origin);
for(const role of Object.keys(f.roles)){
 const c=await b.newContext({viewport:{width:1440,height:1000},reducedMotion:'reduce'}),p=await c.newPage();let writes=0,legacy=0;const errors=[];
 p.on('pageerror',e=>errors.push(e.message));p.on('request',r=>{const u=new URL(r.url());if(u.pathname.startsWith('/api/patients')&&!['GET','HEAD'].includes(r.method()))writes++;if(u.pathname==='/api/patients'&&r.method()==='GET'&&u.searchParams.get('view')!=='page')legacy++;});
 await login(p,f.roles[role].username);await p.evaluate(id=>app.openPatient(id),role==='consilium_chair'?f.chair.id:f.card.id);await p.waitForFunction(()=>document.querySelector('#patientFacts').children.length===3);
 assert.equal(await p.getByRole('tab').count(),8);assert.equal(await p.locator('#patientRoute .timeline').count(),1);
 await p.evaluate(fs.readFileSync(process.env.AXE_SOURCE||path.join(__dirname,'../wp_h/node_modules/axe-core/axe.min.js'),'utf8'));
 for(const [key,label] of Object.entries(tabs)){
  await tab(p,key);
  if(key==='notifications')await p.waitForFunction(()=>!document.querySelector('#patientNotificationsBlock').textContent.includes('Загрузка…'));
  if(key==='overview')await p.waitForFunction(()=>!document.querySelector('#patientAttentionBlock').textContent.includes('Загрузка…')&&!document.querySelector('#patientPortalAccessBlock').textContent.includes('Загрузка…'));
  assert.equal(await p.locator('#patientPanels').getAttribute('aria-labelledby'),'tab-'+key);
  assert.equal(await p.locator('#patientRoute .timeline').count(),1);
  for(const theme of ['dark','light']){await p.evaluate(t=>app.applyTheme(t),theme);for(const width of [1440,375]){
   await p.setViewportSize({width,height:width===1440?1000:812});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
   if(role==='developer')await p.screenshot({path:path.join(out,`${key}-${theme}-${width}.png`),animations:'disabled'});
   const violations=await p.evaluate(async()=>{await Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{})));const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});results.axe.push({role,key,theme,width,violations});
  }}
 }
 assert.equal(writes,0);assert.equal(legacy,0);assert.deepEqual(errors,[]);
 if(['deputy_chief_oncology','caop_head'].includes(role)){assert.equal(await p.locator('#patientRoute button').count(),0);assert.equal(await p.locator('#patientRoute [aria-label]').evaluateAll(nodes=>nodes.some(n=>/Перевести пациента|Показать даты/.test(n.getAttribute('aria-label')))),false);assert.equal(await p.locator('#patientPanels input:not([disabled]),#patientPanels textarea:not([disabled])').count(),0);}
 if(role==='developer'){
  await p.setViewportSize({width:1440,height:1000});await tab(p,'data');await p.getByRole('combobox',{name:'Локализация пациента',exact:true}).selectOption('');assert.equal(await p.evaluate(()=>app.dirtyClinicalControls().length),0);await tab(p,'history');assert.equal(await p.locator('#confirmModal').isVisible(),false);await tab(p,'data');await p.locator('#patientPanels input[type="tel"]').fill('+70000000999');await tab(p,'overview');assert.equal(writes,0);let draft=await p.evaluate(id=>app.sessionFieldDrafts.get(id+':phone'),f.card.id);assert.equal(draft.body.value,'+70000000999');assert.equal(draft.body.expectedVersion,f.card.version);
  await p.evaluate(()=>app.renderPatientView());assert.equal(writes,0);assert.equal(await p.evaluate(()=>app.sessionFieldDrafts.size),1);
  // Explicit original-version save remains one independent PATCH.
  let response=p.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith('/api/patients/'+f.card.id));await p.getByRole('button',{name:'Сохранить правку',exact:true}).click();assert.equal((await response).status(),200);await p.waitForFunction(()=>app.sessionFieldDrafts.size===0);assert.equal(writes,1);
  // Unsubmitted event input cannot silently save or disappear on tab navigation.
  await tab(p,'events');const field=p.getByRole('textbox',{name:'Примечание к этапу',exact:true}).first();await field.fill('Синтетическая несохраненная заметка');await p.getByRole('tab',{name:'История',exact:true}).click();assert.equal(await p.locator('#confirmModal').isVisible(),true);assert.equal(writes,1);await p.locator('#confirmCancelBtn').click();assert.equal(await p.evaluate(()=>app.currentTab),'events');assert.equal(await field.inputValue(),'Синтетическая несохраненная заметка');
  response=p.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith('/history/0/note'));await p.getByRole('button',{name:'Сохранить заметку',exact:true}).click();assert.equal((await response).status(),200);await p.waitForFunction(()=>!app.clinicalWriteInFlight);assert.equal(writes,2);
  results.checks.push('no auto-write during tab/rerender; original-version field draft; one manual PATCH; unsaved clinical navigation confirmation; explicit note save');
  // True server version conflict: preserve the user's original-version draft.
  await tab(p,'data');const version=await p.evaluate(id=>app.getPatient(id).version,f.card.id);await p.locator('#patientPanels input[type="tel"]').fill('+70000000888');await tab(p,'history');await f.call('PATCH','/api/patients/'+f.card.id,{field:'phone',value:'+70000000777',expectedVersion:version});response=p.waitForResponse(r=>r.request().method()==='PATCH'&&r.url().endsWith('/api/patients/'+f.card.id));await p.getByRole('button',{name:'Сохранить правку',exact:true}).click();assert.equal((await response).status(),409);assert.equal((await f.call('GET','/api/patients/'+f.card.id)).phone,'+70000000777');assert.equal(await p.evaluate(id=>app.sessionFieldDrafts.get(id+':phone').body.expectedVersion,f.card.id),version);results.checks.push('real409 retains original version; server data not overwritten');
  // Large real history, repeated stages, long strings; no medical writes while viewing.
  const before=writes;await p.evaluate(id=>app.openPatient(id),f.large.id);assert.equal(await p.evaluate(()=>app.getPatient(app.currentPatientId).history.length),31);
  for(const key of Object.keys(tabs)){const t=Date.now();await tab(p,key);const data=await p.evaluate(()=>({tab:app.currentTab,dom:document.querySelectorAll('*').length,history:app.getPatient(app.currentPatientId).history.length}));results.metrics.push({...data,wallMs:Date.now()-t});}
  assert.equal(writes,before);for(const width of [1440,375,320]){await p.setViewportSize({width,height:812});assert.equal(await p.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false,JSON.stringify(await p.evaluate(()=>[...document.querySelectorAll('*')].filter(n=>n.getBoundingClientRect().right>innerWidth+1).map(n=>({tag:n.tagName,cls:n.className,right:n.getBoundingClientRect().right})).slice(0,25))));}results.checks.push('31 history occurrences; repeated stages; long fields; bounded page width; no viewing writes');
 }
 await c.close();
}
assert.deepEqual(results.axe.flatMap(x=>x.violations),[]);fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(results,null,2));console.log(JSON.stringify({checks:results.checks,axeStates:results.axe.length,violations:results.axe.flatMap(x=>x.violations),metrics:results.metrics}));await f.admin.close();
}finally{fs.writeFileSync(path.join(out,'partial-results.json'),JSON.stringify(results,null,2));await b.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});
