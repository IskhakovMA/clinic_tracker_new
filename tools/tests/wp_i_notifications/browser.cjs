/* I-6A: real localhost FastAPI + disposable synthetic SQLite. No providers. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),assert=require('assert/strict'),fs=require('fs'),path=require('path');
const {setup,login,password}=require('../wp_i_consilium/fixtures.cjs');
(async()=>{const origin=process.env.WP_H_ORIGIN,out=process.env.OUT,baseline=process.env.I6_BASELINE==='1';const b=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});const result={baseline,checks:[],roles:[],axe:[],serverDenials:[],transportFaults:[],screens:[],realBackend:true};
try{
const f=await setup(b,origin),templates=(await f.api('GET','/api/notifications/templates')).templates,template=templates.find(t=>t.enabled);assert(template);
const patients=[];const when=new Date(Date.now()+3600000).toISOString();
for(let i=0;i<5;i++){
 const p=await f.api('POST','/api/patients',{name:'Синтетический I6A '+i,age:45,phone:'+79991234567',priority:'normal',stage:'exam',diagnosis:'C34.1',nosology:'lung34',head:'Синтетический Заведующий',treatingDoctorUserId:f.roles.doctor.id,events:[{date:'2001-01-01',time:'10:00'}]});patients.push(p);
 await f.api('PUT','/api/notifications/patients/'+p.id+'/preferences',{smsEnabled:true,consentStatus:'granted'});
 for(let j=0;j<5;j++)await f.api('POST','/api/notifications',{patientId:p.id,templateCode:template.code,scheduledAt:new Date(Date.now()+3600000+j*60000).toISOString()});
}
async function center(page,user){await login(page,origin,user);await page.evaluate(async()=>{app.notifications.activeTab='inbox';app.showView('notifications');await app.renderInternalNotificationCenter();});await page.waitForFunction(()=>app.internalNotifications.fullItems.length>0);}
async function shot(page,name){await page.evaluate(()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r))));await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));if(!baseline){const dimensions=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));fs.writeFileSync(path.join(out,name+'-dimensions.json'),JSON.stringify(dimensions));assert.equal(dimensions.scroll,dimensions.width,'stable screenshot reflow '+name);}await page.screenshot({path:path.join(out,name+'.png'),fullPage:true});result.screens.push(name+'.png');}
const c=await b.newContext({reducedMotion:'reduce'}),p=await c.newPage();await center(p,'wph.dev');
for(const theme of ['light','dark'])for(const width of [1440,375,320]){await p.setViewportSize({width,height:1000});await p.evaluate(t=>app.applyTheme(t),theme);await shot(p,`center-${theme}-${width}`);}
if(baseline){
 await p.evaluate(async()=>{app.notifications.activeTab='outbound';await app.renderNotificationsPage()});
 for(const theme of ['light','dark'])for(const width of [1440,375,320]){await p.setViewportSize({width,height:1000});await p.evaluate(t=>app.applyTheme(t),theme);await shot(p,`outbound-${theme}-${width}`);}
 await p.evaluate(async id=>{const card=await apiGetJson('/api/patients/'+id);app.patientDetails.set(id,card);app.openPatientTab(id,'notifications')},patients[0].id);await p.waitForFunction(()=>document.querySelector('#patientNotificationTemplate'));await p.waitForFunction(()=>!document.querySelectorAll('#toasts .toast').length);
 for(const theme of ['light','dark'])for(const width of [1440,375,320]){await p.setViewportSize({width,height:1000});await p.evaluate(t=>app.applyTheme(t),theme);await shot(p,`patient-${theme}-${width}`);}
 fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));return;}
assert.equal(await p.evaluate(()=>app.internalReady.full),true);
const summary=await f.call(c,'GET','/api/internal-notifications/summary');assert.equal(await p.locator('#internalNotificationsBadge').textContent(),String(summary.unread_count));assert(summary.unread_count>0);
result.checks.push('exact server unread, not DOM/page count');
const first=await p.evaluate(()=>app.internalNotifications.fullItems[0]);
let reached,release;const arrived=new Promise(r=>reached=r),held=new Promise(r=>release=r);let marks=0;
await p.route('**/api/internal-notifications/*/read',async route=>{marks++;reached();await held;await route.continue()});
await p.evaluate(id=>{window.mark=app.markInternalNotificationRead(id)},first.id);await arrived;
assert.equal(await p.locator('#internalNotificationsFullReadAllBtn').isDisabled(),true);
await p.evaluate(id=>Promise.all([app.markInternalNotificationRead(id),app.markAllInternalNotificationsRead()]),first.id);assert.equal(marks,1);release();await p.evaluate(()=>window.mark);await p.unrouteAll({behavior:'wait'});assert.equal(await p.evaluate(()=>app.notificationMutation),null);
assert.equal((await f.call(c,'GET','/api/internal-notifications/summary')).unread_count,summary.unread_count-1);
result.checks.push('mark/read-all race: one pending write, no conflicting second operation, exact fresh counter');
// A second authenticated context marks all; refresh must use server state.
const other=await b.newContext();await f.call(other,'POST','/api/auth/login',{username:'wph.dev',password});await f.call(other,'POST','/api/internal-notifications/read-all',{});await p.evaluate(()=>app.renderInternalNotificationCenter());assert.equal(await p.locator('#internalNotificationsBadge').textContent(),'0');
result.checks.push('second-context read-all reflected by authoritative server refresh');
// Outbound pagination, not an initial100-row slice.
await p.evaluate(async()=>{app.notifications.activeTab='outbound';await app.renderNotificationsPage()});assert.equal(await p.locator('#notificationsBody tr').count(),20);await p.locator('#notificationsNextBtn').click();await p.waitForFunction(()=>app.notifications.ready&&app.notifications.offset===20);assert.equal(await p.locator('#notificationsBody tr').count(),5);await p.locator('#notificationsPrevBtn').click();await p.waitForFunction(()=>app.notifications.ready&&app.notifications.offset===0);
result.checks.push('server outbound pagination20+5, total25; no local replacement');
await p.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));result.outboundAxe=[];
for(const theme of ['light','dark'])for(const width of [1440,375,320]){await p.setViewportSize({width,height:1000});await p.evaluate(t=>app.applyTheme(t),theme);await shot(p,`outbound-${theme}-${width}`);const violations=await p.evaluate(async()=>{const a=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return a.violations.map(v=>({id:v.id,nodes:v.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});fs.writeFileSync(path.join(out,`outbound-${theme}-${width}-axe.json`),JSON.stringify(violations,null,2));assert.deepEqual(violations,[]);result.outboundAxe.push({theme,width,violations});}
// Search/filter are sent to the server rather than filtering an initial page.
await p.evaluate(async()=>{app.notifications.filters.query='Синтетический I6A 0';app.notifications.offset=0;await app.renderNotifications()});assert.equal(await p.locator('#notificationsBody tr').count(),5);assert.equal(await p.evaluate(()=>app.notifications.total),5);await p.evaluate(async()=>{app.notifications.filters.query='';app.notifications.offset=0;await app.renderNotifications()});
const doctor=await p.evaluate(()=>app.notifications.items[0].treating_doctor_name);await p.evaluate(async doctor=>{app.notifications.filters.doctor=doctor;app.notifications.filters.query='Синтетический нет такого пациента I6';await app.renderNotifications()},doctor);assert.equal(await p.locator('#notificationsBody tr').count(),0);assert.equal(await p.locator('#notificationsDoctorFilter').inputValue(),doctor);await p.evaluate(async()=>{app.notifications.filters.doctor='';app.notifications.filters.query='';await app.renderNotifications()});
result.checks.push('server search across outbound pages, total5 then25; selected doctor retained with empty server page');
// Real conflicting cancel, not route.fulfill.
const id=await p.evaluate(()=>app.notifications.items.find(i=>i.status==='pending').id);await f.call(other,'POST','/api/notifications/'+id+'/cancel',{});
const conflict=p.waitForResponse(r=>r.url().endsWith('/'+id+'/cancel'));await p.evaluate(id=>app.cancelNotification(id),id);assert.equal((await conflict).status(),409);assert.equal(await p.locator('#notificationsBody tr').count(),0);await p.evaluate(()=>app.cancelNotification('not-current'));await p.evaluate(()=>app.renderNotifications());assert.equal(await p.evaluate(()=>app.notificationMutation),null);result.serverDenials.push('real409 concurrent cancel');
// Old200 cannot revive counter or actions after a NEW real server read-audit503.
let oldReached,oldRelease;const oldArrived=new Promise(r=>oldReached=r),oldHeld=new Promise(r=>oldRelease=r);let reads=0;
await p.route('**/api/internal-notifications/summary',async route=>{if(++reads===1){const response=await route.fetch();assert.equal(response.status(),200);oldReached();await oldHeld;await route.fulfill({response})}else await route.continue()});
await p.evaluate(()=>{window.oldSummary=app.refreshInternalNotificationSummary()});await oldArrived;await c.request.post(origin+'/synthetic/i6/fail-read-once');const failure=p.waitForResponse(r=>r.url().endsWith('/summary')&&r.status()===503);await p.evaluate(()=>app.refreshInternalNotificationSummary());await failure;oldRelease();await p.evaluate(()=>window.oldSummary);assert.equal(await p.evaluate(()=>app.internalNotifications.summary),null);assert.equal(await p.evaluate(()=>app.internalSummaryReady),false);assert.equal(await p.locator('#internalNotificationsSubtitle').textContent(),'Счетчик временно недоступен');assert.equal(await p.locator('#internalNotificationsFullReadAllBtn').isDisabled(),true);await p.unrouteAll({behavior:'wait'});result.serverDenials.push('real read-audit503 via synthetic test fault');result.transportFaults.push('held older real200 summary');
await p.evaluate(async()=>{app.notifications.activeTab='inbox';await app.renderNotificationsPage()});
// Drawer keyboard focus and return.
await p.locator('#internalNotificationsBell').click();await p.waitForFunction(()=>app.internalReady.drawer);await p.locator('#internalNotificationsFullBtn').focus();await p.keyboard.press('Tab');assert.equal(await p.evaluate(()=>document.activeElement.id),'internalNotificationsClose');await p.keyboard.press('Escape');await p.locator('#internalNotificationsDrawer').waitFor({state:'hidden'});assert.equal(await p.evaluate(()=>document.activeElement.id),'internalNotificationsBell');
// Patient consent/preview create, Moscow scheduling, no automatic writes on reads.
await p.evaluate(async id=>{await app.openNotificationPatient(id,'notifications');},patients[0].id);await p.waitForFunction(()=>app.patientNotificationReady);assert((await p.locator('#patientNotificationsBlock').textContent()).includes('Согласие получено'));assert(await p.locator('#patientNotificationPreview').isEditable()===false);
await p.waitForFunction(()=>!document.querySelectorAll('#toasts .toast').length);for(const theme of ['light','dark'])for(const width of [1440,375,320]){await p.setViewportSize({width,height:1000});await p.evaluate(t=>app.applyTheme(t),theme);await shot(p,`patient-${theme}-${width}`);}
let patientWrites=0;p.on('request',r=>{if(r.url().includes('/api/notifications')&&!['GET','HEAD'].includes(r.method()))patientWrites++});await p.evaluate(()=>app.renderPatientNotificationsBlock(app.currentPatientId));assert.equal(patientWrites,0);
// Newer503 transport refusal and older patient200 remain a separate test class.
let detailReached,detailRelease,n=0;const da=new Promise(r=>detailReached=r),dh=new Promise(r=>detailRelease=r);
await p.route('**/api/notifications/patients/'+patients[0].id,async route=>{if(++n===1){const response=await route.fetch();detailReached();await dh;await route.fulfill({response})}else await route.fulfill({status:503,contentType:'application/json',body:'{"detail":"Synthetic transport503"}'})});await p.evaluate(()=>{window.oldPatient=app.renderPatientNotificationsBlock(app.currentPatientId)});await da;await p.evaluate(()=>app.renderPatientNotificationsBlock(app.currentPatientId));detailRelease();await p.evaluate(()=>window.oldPatient);assert.equal(await p.locator('#patientNotificationTemplate').count(),0);assert.equal(await p.evaluate(()=>app.patientNotificationReady),false);await p.unrouteAll({behavior:'wait'});result.transportFaults.push('newer injected patient503 before older real200');await p.evaluate(()=>app.renderPatientNotificationsBlock(app.currentPatientId));
// Six roles,2themes,3widths with real API permissions and axe.
for(const [role,user] of Object.entries(f.roles)){
 const context=await b.newContext({reducedMotion:'reduce'}),page=await context.newPage();await login(page,origin,user.username);await page.evaluate(async()=>{app.showView('notifications');if(app.currentView==='notifications')await app.renderNotificationsPage()});
 const view=await page.evaluate(()=>app.currentView);const retry=await f.request(context,'POST','/api/notifications/missing/retry',{});assert.equal(retry.status(),role==='developer'?404:403);result.roles.push({role,view,retry:retry.status()});
 await page.evaluate(fs.readFileSync(process.env.AXE_SOURCE,'utf8'));
 for(const theme of ['light','dark'])for(const width of [1440,375,320]){await page.setViewportSize({width,height:1000});await page.evaluate(t=>app.applyTheme(t),theme);await page.evaluate(()=>Promise.all(document.getAnimations().map(a=>a.finished.catch(()=>{}))));const violations=await page.evaluate(async()=>{const a=await axe.run(document,{runOnly:{type:'tag',values:['wcag2a','wcag2aa','wcag21aa']}});return a.violations.map(x=>({id:x.id,nodes:x.nodes.map(n=>({target:n.target,summary:n.failureSummary}))}))});assert.deepEqual(violations,[]);assert.equal(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth),false);result.axe.push({role,theme,width,violations});await shot(page,`role-${role}-${theme}-${width}`);}
 await context.close();
}
result.serverDenials.push('six-role actual retry403 / developer404');
// Genuine session loss while an older200 is waiting; medical data cannot reappear.
await p.evaluate(async()=>{app.notifications.activeTab='inbox';app.showView('notifications');await app.renderInternalNotificationCenter()});
let sr,sp;const sa=new Promise(r=>sr=r),sh=new Promise(r=>sp=r);
await p.route('**/api/internal-notifications/summary',async route=>{const response=await route.fetch();sr();await sh;await route.fulfill({response})});await p.evaluate(()=>{window.pre401=app.refreshInternalNotificationSummary()});await sa;
await f.api('POST','/api/users/'+(await p.evaluate(()=>app.user.id))+'/terminate-sessions',{});await p.evaluate(()=>apiGetJson('/api/patients').catch(()=>{}));await p.waitForFunction(()=>app.sessionExpired);sp();await p.evaluate(()=>window.pre401);assert.equal(await p.evaluate(()=>app.internalSummaryReady),false);await p.unrouteAll({behavior:'wait'});result.serverDenials.push('real401 expired session with held summary200');
await other.close();await c.close();await f.admin.close();await f.chair.close();
fs.writeFileSync(path.join(out,'results.json'),JSON.stringify(result,null,2));
}finally{await b.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});
