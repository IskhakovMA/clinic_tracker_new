// I-5B: real FastAPI medical transitions. Transport loss is explicitly injected after commit.
const {chromium}=require(process.env.PLAYWRIGHT_MODULE), assert=require('assert/strict'), fs=require('fs'), path=require('path');
const {setup,login,password}=require('./fixtures.cjs');
(async()=>{
 const origin=process.env.WP_H_ORIGIN,b=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 const results={checks:[],axe:[],roles:[],realBackend:true,transportFaults:[]};
 try {
  const f=await setup(b,origin),ctx=await b.newContext({reducedMotion:'reduce'}),p=await ctx.newPage();
  async function workspace(page,user,authenticated=false){if(authenticated){await page.goto(origin);await page.waitForFunction(()=>app.appReady&&!app.sessionExpired);}else await login(page,origin,user);await page.evaluate(async day=>{app.consilium.dateFilter=day;app.consilium.doctorDate=day;app.showView('consilium');await app.renderConsilium()},f.day);await page.waitForFunction(()=>app.consiliumGridReady())}
  const doc=await b.newContext(),chair2=await b.newContext();
  await f.call(doc,'POST','/api/auth/login',{username:f.roles.doctor.username,password});
  await f.call(chair2,'POST','/api/auth/login',{username:f.roles.consilium_chair.username,password});
  const record=await f.call(doc,'POST','/api/consilium/appointments',f.booking(f.patients[0].id,'10:00'));
  await workspace(p,f.roles.consilium_chair.username);
  // Reproducible six-role presentation; opening screens is never a medical write.
  for(const [role,user] of Object.entries(f.roles)){
   const c=await b.newContext({reducedMotion:'reduce'}),page=await c.newPage();await login(page,origin,user.username);
   let writes=0;page.on('request',r=>{if(r.url().includes('/api/consilium/')&&!['GET','HEAD'].includes(r.method()))writes++});
   await page.evaluate(async day=>{app.consilium.dateFilter=day;app.consilium.doctorDate=day;app.showView('consilium');if(app.currentView==='consilium')await app.renderConsilium()},f.day);
   assert.equal(await page.locator('#consiliumReviewWorkspace').isVisible(),role==='consilium_chair');
   const trigger=page.locator('#consiliumReviewList li').filter({hasText:f.patients[0].name}).getByRole('button',{name:'Рассмотреть',exact:true});
   if(role==='consilium_chair')await trigger.click();else await page.evaluate(id=>app.openConsiliumDecision(id),record.id);
   assert.equal(await page.locator('#consiliumDecisionModal').isVisible(),role==='consilium_chair');
   await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
   for(const theme of ['light','dark'])for(const width of [1440,375,320]){
    await page.setViewportSize({width,height:1000});await page.evaluate(t=>app.applyTheme(t),theme);
    const v=await page.evaluate(async()=>{const r=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return r.violations.map(x=>({id:x.id,nodes:x.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});
    results.axe.push({role,theme,width,violations:v});assert.deepEqual(v,[]);
    assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);
    await page.screenshot({path:path.join(process.env.OUT,`decision-${role}-${theme}-${width}.png`),fullPage:true});
   }
   if(role==='consilium_chair'){
    await page.locator('#consiliumTreatmentType').focus();await page.keyboard.press('Tab');assert(await page.locator('#consiliumDecisionModal').evaluate(n=>n.contains(document.activeElement)));
    await page.keyboard.press('Escape');await page.locator('#consiliumDecisionModal').waitFor({state:'hidden'});assert.equal(await trigger.evaluate(n=>n===document.activeElement),true);
   }
   assert.equal(writes,0);results.roles.push({role,writes,chair:role==='consilium_chair'});await c.close();
  }
  results.checks.push('six real roles, both themes 1440/375/320, zero writes on open; developer cannot decide');
  await p.evaluate(id=>app.openConsiliumDecision(id),record.id);
  await p.locator('#consiliumTreatmentType').selectOption('chemotherapy');await p.locator('#consiliumTreatmentScheme').fill('Синтетическая схема I5B');
  await p.locator('#closeConsiliumDecisionIconBtn').click();await p.locator('#confirmModal').waitFor({state:'visible'});await p.locator('#confirmCancelBtn').click();assert.equal(await p.locator('#consiliumTreatmentScheme').inputValue(),'Синтетическая схема I5B');
  // Concurrent start, explicit request barrier. Exactly one state transition succeeds.
  let reached,release;const arrived=new Promise(r=>reached=r),held=new Promise(r=>release=r);
  await p.route('**/appointments/'+record.id+'/review',async route=>{reached();await held;await route.continue()});
  const review=p.waitForResponse(r=>r.url().endsWith('/'+record.id+'/review'));
  await p.locator('#startConsiliumReviewBtn').click();await arrived;
  assert.equal(await p.locator('#startConsiliumReviewBtn').isDisabled(),true);assert.equal(await p.locator('#consiliumTreatmentType').isDisabled(),true);
  await p.evaluate(()=>app.startConsiliumReview());
  const other=await f.request(chair2,'POST','/api/consilium/appointments/'+record.id+'/review',{});assert.equal(other.status(),200);
  release();assert.equal((await review).status(),409);await p.unrouteAll({behavior:'wait'});
  await p.waitForFunction(()=>app.consilium.decisionContext.blocked&&!app.consilium.decisionContext.busy);
  assert.equal(await p.locator('#consiliumTreatmentScheme').inputValue(),'Синтетическая схема I5B');
  await p.locator('#refreshConsiliumDecisionBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext.record.status==='in_review'&&!app.consilium.decisionContext.busy);
  assert.equal(await p.locator('#signConsiliumBtn').isEnabled(),true);assert.equal(await p.locator('#consiliumTreatmentScheme').inputValue(),'Синтетическая схема I5B');
  results.checks.push('dirty close confirmation, concurrent review genuine200/409, pending lock, explicit refresh preserves draft');
  // Return preserves a chronological server history; revision modal retains decision underneath.
  await p.locator('#returnConsiliumBtn').click();await p.locator('#consiliumRevisionComment').fill('Синтетическое уточнение раунд 1');
  await p.route('**/appointments/'+record.id+'/return',route=>route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({detail:'Synthetic transport503 before return'})}));
  await p.locator('#confirmConsiliumRevisionBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext.blocked&&!app.consilium.decisionContext.busy);
  assert.equal(await p.locator('#consiliumRevisionComment').inputValue(),'Синтетическое уточнение раунд 1');assert.equal(await p.locator('#confirmConsiliumRevisionBtn').isDisabled(),true);
  await p.unrouteAll({behavior:'wait'});await p.locator('#refreshConsiliumRevisionBtn').click();await p.waitForFunction(()=>!app.consilium.decisionContext.busy&&!app.consilium.decisionContext.blocked);
  assert.equal(await p.locator('#consiliumRevisionComment').inputValue(),'Синтетическое уточнение раунд 1');results.transportFaults.push('return503 before mutation, input preserved until explicit read');
  const returned=p.waitForResponse(r=>r.url().endsWith('/'+record.id+'/return'));
  await p.locator('#confirmConsiliumRevisionBtn').click();assert.equal((await returned).status(),200);
  await p.locator('#consiliumDecisionModal').waitFor({state:'hidden'});
  const dp=await doc.newPage();await workspace(dp,f.roles.doctor.username,true);
  await dp.evaluate(id=>app.openConsiliumModal(id),record.id);await dp.locator('#consiliumComment').fill('Синтетическое исправление раунд 1');
  const resub=dp.waitForResponse(r=>r.url().endsWith('/'+record.id+'/resubmit'));
  await dp.locator('#resubmitConsiliumBtn').click();assert.equal((await resub).status(),200);await dp.locator('#consiliumModal').waitFor({state:'hidden'});
  let history=(await f.call(ctx,'GET','/api/consilium/appointments/'+record.id+'/revisions')).revisions;
  assert.equal(history.length,1);assert.equal(history[0].comment,'Синтетическое уточнение раунд 1');assert(history[0].resubmittedAt);
  await p.evaluate(()=>app.renderConsilium());await p.evaluate(id=>app.openConsiliumDecision(id),record.id);
  await p.locator('#startConsiliumReviewBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext?.record.status==='in_review'&&!app.consilium.decisionContext.busy);
  results.checks.push('real return, versioned correction and separate resubmit; round history retained; repeat review explicit');
  // Genuine server422: repeating the prior return comment is forbidden; no history round is added.
  await p.locator('#returnConsiliumBtn').click();await p.locator('#consiliumRevisionComment').fill('Синтетическое уточнение раунд 1');
  const duplicateComment=p.waitForResponse(r=>r.url().endsWith('/'+record.id+'/return'));
  await p.locator('#confirmConsiliumRevisionBtn').click();assert.equal((await duplicateComment).status(),422);
  assert.equal(await p.locator('#consiliumRevisionComment').inputValue(),'Синтетическое уточнение раунд 1');
  assert.equal((await f.call(ctx,'GET','/api/consilium/appointments/'+record.id+'/revisions')).revisions.length,1);
  await p.locator('#cancelConsiliumRevisionBtn').click();await p.locator('#confirmOkBtn').click();assert.equal(await p.locator('#consiliumDecisionModal').isVisible(),true);
  results.checks.push('genuine duplicate-comment422 retains input, no false return history, explicit discard returns to decision');
  // Losing the response AFTER a real signed commit must never cause an automatic second POST.
  const stageBefore=(await f.call(doc,'GET','/api/patients/'+record.patientId)).stage;
  await p.locator('#consiliumTreatmentType').selectOption('surgery');let signPosts=0;
  p.on('request',r=>{if(r.method()==='POST'&&r.url().endsWith('/'+record.id+'/sign'))signPosts++});
  await p.route('**/appointments/'+record.id+'/sign',async route=>{const committed=await route.fetch();assert.equal(committed.status(),200);await route.abort('connectionreset')});
  await p.locator('#signConsiliumBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext?.blocked&&!app.consilium.decisionContext.busy);
  assert.equal(signPosts,1);assert.equal(await p.locator('#consiliumTreatmentType').inputValue(),'surgery');assert.equal(await p.locator('#signConsiliumBtn').isDisabled(),true);
  await p.evaluate(()=>app.submitConsiliumDecision({preventDefault(){}}));assert.equal(signPosts,1);
  await p.unrouteAll({behavior:'wait'});await p.locator('#refreshConsiliumDecisionBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext?.record.status==='signed'&&!app.consilium.decisionContext.busy);
  assert.equal(signPosts,1);assert.equal(await p.locator('#signConsiliumBtn').isDisabled(),true);
  const signed=(await f.call(ctx,'GET','/api/consilium/appointments')).appointments.find(x=>x.id===record.id);
  assert.equal(signed.status,'signed');assert.equal(signed.treatmentType,'surgery');assert.equal((await f.call(doc,'GET','/api/patients/'+record.patientId)).stage,stageBefore);
  results.transportFaults.push('sign response aborted only after genuine server200/commit');
  results.checks.push('lost committed sign response: one POST, draft retained, locked repeat, explicit state read confirms signature, patient stage unchanged');
  const patient=await f.call(f.admin,'GET','/api/patients/'+record.patientId);
  // Existing independent field endpoint, not a new rule. Signed projection remains immutable.
  await f.call(f.admin,'PATCH','/api/patients/'+record.patientId,{field:'diagnosis',value:'C34.9',expectedVersion:patient.version});
  const after=(await f.call(ctx,'GET','/api/consilium/appointments')).appointments.find(x=>x.id===record.id);
  assert.deepEqual(after,signed);
  await p.locator('#closeConsiliumDecisionIconBtn').click();await p.locator('#confirmOkBtn').click();
  await p.evaluate(item=>app.openConsiliumDetails(item),after);await p.locator('#consiliumDetailsRevisions').getByText('Синтетическое уточнение раунд 1',{exact:true}).waitFor();
  assert((await p.locator('#consiliumDetailsBody').textContent()).includes('C34.1'));assert(!(await p.locator('#consiliumDetailsBody').textContent()).includes('C34.9'));
  results.medicalEvidence={signedBeforePatientChange:signed,signedAfterPatientChange:after,revisionHistory:history,patientStageBeforeSign:stageBefore,patientStageAfterSign:(await f.call(doc,'GET','/api/patients/'+record.patientId)).stage,signPostsAfterLostResponse:signPosts,concurrentReviewStatuses:[200,409]};
  results.checks.push('signed medical snapshot unchanged after current patient diagnosis update; return history visible in separate detail view');
  await p.locator('#closeConsiliumDetailsBtn').click();
  // Real stale-edit 409, a second employee, original comment retained.
  const second=await f.call(doc,'POST','/api/consilium/appointments',f.booking(f.patients[1].id,'11:00'));
  await dp.evaluate(async id=>{await app.renderConsilium();await app.openConsiliumModal(id)},second.id);
  const head=await b.newContext();await f.call(head,'POST','/api/auth/login',{username:f.roles.department_head.username,password});
  await f.call(head,'PUT','/api/consilium/appointments/'+second.id,{...f.booking(second.patientId,'11:00'),version:second.version,comment:'Синтетическая серверная правка'});
  await dp.locator('#consiliumComment').fill('Синтетический старый ввод');const conflict=dp.waitForResponse(r=>r.request().method()==='PUT'&&r.url().endsWith('/'+second.id));await dp.locator('#submitConsiliumBtn').click();assert.equal((await conflict).status(),409);assert.equal(await dp.locator('#consiliumComment').inputValue(),'Синтетический старый ввод');
  results.checks.push('two employees: genuine stale version409 retains correction without overwriting server');
  // A lost PUT response is confirmed on the server but never blindly repeated.
  await dp.locator('#consiliumConflictRefreshBtn').click();await dp.waitForFunction(()=>!app.consilium.refreshPending&&document.getElementById('consiliumConflict').classList.contains('is-hidden'));
  await dp.locator('#consiliumComment').fill('Синтетический commit без ответа');let puts=0;
  dp.on('request',r=>{if(r.method()==='PUT'&&r.url().endsWith('/'+second.id))puts++});
  let putReached,putRelease;const putArrived=new Promise(r=>putReached=r),putHeld=new Promise(r=>putRelease=r);
  await dp.route('**/appointments/'+second.id,async route=>{if(route.request().method()!=='PUT')return route.continue();const response=await route.fetch();assert.equal(response.status(),200);putReached();await putHeld;await route.abort('connectionreset')});
  await dp.locator('#submitConsiliumBtn').click();await putArrived;assert.equal(await dp.locator('#consiliumForm').evaluate(n=>n.inert),true);assert.equal(await dp.locator('#deleteConsiliumBtn').isDisabled(),true);await dp.locator('#closeConsiliumBtn').click();assert.equal(await dp.locator('#consiliumModal').isVisible(),true);await dp.evaluate(()=>app.resubmitConsiliumAppointment());assert.equal(puts,1);putRelease();await dp.waitForFunction(()=>!document.getElementById('consiliumConflict').classList.contains('is-hidden'));
  assert.equal(puts,1);assert.equal(await dp.locator('#consiliumComment').inputValue(),'Синтетический commit без ответа');await dp.evaluate(()=>app.submitConsiliumAppointment({preventDefault(){}}));assert.equal(puts,1);
  await dp.unrouteAll({behavior:'wait'});assert.equal((await f.call(doc,'GET','/api/consilium/appointments')).appointments.find(x=>x.id===second.id).comment,'Синтетический commit без ответа');
  results.transportFaults.push('PUT aborted only after genuine200/commit');results.checks.push('lost correction response: original input retained, no automatic or direct repeat before explicit refresh');
  const third=await f.call(doc,'POST','/api/consilium/appointments',f.booking(f.patients[2].id,'11:30'));
  await p.evaluate(async id=>{await app.renderConsilium();app.openConsiliumDecision(id)},third.id);
  await p.locator('#startConsiliumReviewBtn').click();await p.waitForFunction(()=>app.consilium.decisionContext?.record.status==='in_review'&&!app.consilium.decisionContext.busy);
  await p.locator('#consiliumTreatmentType').selectOption('active_observation');
  const positiveSign=p.waitForResponse(r=>r.url().endsWith('/'+third.id+'/sign'));await p.locator('#signConsiliumBtn').click();assert.equal((await positiveSign).status(),200);await p.locator('#consiliumDecisionModal').waitFor({state:'hidden'});
  results.checks.push('confirmed successful sign closes form and refreshes queue without an automatic stage change');
  // Identity switch in shared cookie context clears the old clinical input, no writes.
  await p.evaluate(()=>app.renderConsilium());await p.evaluate(id=>app.openConsiliumDecision(id),second.id);
  await p.locator('#consiliumTreatmentType').selectOption('chemotherapy');await p.locator('#consiliumTreatmentScheme').fill('Синтетический ввод председателя');
  const peer=await ctx.newPage();await peer.goto(origin);await f.call(ctx,'POST','/api/auth/logout',{});await peer.reload();await peer.locator('#username').fill(f.roles.doctor.username);await peer.locator('#password').fill(password);await peer.locator('#loginSubmit').click();await peer.waitForFunction(()=>app.appReady&&!app.sessionExpired);
  await p.bringToFront();await p.evaluate(()=>app.reconcilePeerSession());await p.waitForFunction(id=>app.user?.id===id || app.sessionExpired,f.roles.doctor.id);
  assert.equal(await p.locator('#consiliumDecisionModal').isVisible(),false);assert(!(await p.content()).includes('Синтетический ввод председателя'));
  results.checks.push('shared cookie employee change clears chairman decision and draft without medical action');
  // Real 401 while a committed review response is held: stale success must not repopulate a locked workspace.
  const rc=await b.newContext({reducedMotion:'reduce'}),rp=await rc.newPage();await workspace(rp,f.roles.consilium_chair.username);
  await rp.evaluate(id=>app.openConsiliumDecision(id),second.id);let oldReached,oldRelease;const oldArrived=new Promise(r=>oldReached=r),oldHeld=new Promise(r=>oldRelease=r);
  await rp.route('**/appointments/'+second.id+'/review',async route=>{const response=await route.fetch();assert.equal(response.status(),200);oldReached();await oldHeld;await route.fulfill({response})});
  await rp.locator('#startConsiliumReviewBtn').click();await oldArrived;
  await f.api('POST','/api/users/'+f.roles.consilium_chair.id+'/terminate-sessions',{});
  await rp.evaluate(()=>apiGetJson('/api/consilium/schedule').catch(()=>{}));await rp.waitForFunction(()=>app.sessionExpired);
  oldRelease();await rp.unrouteAll({behavior:'wait'});assert.equal(await rp.locator('#consiliumDecisionModal').isVisible(),false);
  await rp.locator('#password').fill(password);await rp.locator('#loginSubmit').click();await rp.waitForFunction(()=>app.appReady&&!app.sessionExpired);
  await rp.evaluate(async id=>{await app.renderConsilium();app.openConsiliumDecision(id)},second.id);
  await rp.locator('#consiliumTreatmentType').selectOption('chemotherapy');await rp.locator('#consiliumTreatmentScheme').fill('Синтетический ввод до отзыва');
  await f.api('PATCH','/api/users/'+f.roles.consilium_chair.id,{roleId:'caop_head',departmentIds:[]});
  await rp.evaluate(()=>apiGetJson('/api/consilium/schedule').catch(()=>{}));await rp.waitForFunction(()=>app.sessionExpired);
  assert.equal(await rp.locator('#consiliumDecisionModal').isVisible(),false);assert(!(await rp.content()).includes('Синтетический ввод до отзыва'));
  results.checks.push('genuine session401 during held review success, same-user reauth, role revocation: no old response or draft restores medical access');await rc.close();
  fs.writeFileSync(path.join(process.env.OUT,'results.json'),JSON.stringify(results,null,2));
  await head.close();await ctx.close();await chair2.close();await doc.close();await f.admin.close();await f.chair.close();
 } finally {await b.close()}
})().catch(e=>{console.error(e.stack);process.exitCode=1});
