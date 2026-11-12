/* Existing mock permanent-failure provider only, never a real send. */
const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),assert=require('assert/strict'),fs=require('fs'),path=require('path');
const {setup,login}=require('../wp_i_consilium/fixtures.cjs');
(async()=>{const origin=process.env.WP_H_ORIGIN,b=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});try{
 const f=await setup(b,origin),c=await b.newContext({reducedMotion:'reduce'}),p=await c.newPage();await login(p,origin,'wph.dev');
 const patient=await f.api('POST','/api/patients',{name:'Синтетический I6 mock retry',age:45,phone:'+79991234567',priority:'normal',stage:'exam',diagnosis:'C34.1',nosology:'lung34',head:'Синтетический Заведующий',treatingDoctorUserId:f.roles.doctor.id,events:[]});
 await f.api('PUT','/api/notifications/patients/'+patient.id+'/preferences',{smsEnabled:true,consentStatus:'granted'});
 const template=(await f.api('GET','/api/notifications/templates')).templates.find(t=>t.enabled);
 const at=Date.now()+3000;const created=await f.api('POST','/api/notifications',{patientId:patient.id,templateCode:template.code,scheduledAt:new Date(at).toISOString()});
 await new Promise(r=>setTimeout(r,Math.max(0,at-Date.now()+100)));
 await f.api('POST','/api/notifications/process-due?limit=100',{});
 const id=created.notification.id,failed=await f.api('GET','/api/notifications/'+id);assert.equal(failed.status,'failed');assert.equal(failed.provider_status,'permanent_failed');
 await p.evaluate(async()=>{app.showView('notifications');app.notifications.activeTab='outbound';await app.renderNotificationsPage()});
 assert.equal(await p.getByRole('button',{name:'Повторить',exact:true}).count(),1);
 let reached,release;const arrived=new Promise(r=>reached=r),hold=new Promise(r=>release=r);let posts=0;
 await p.route('**/api/notifications/'+id+'/retry',async route=>{posts++;reached();await hold;await route.continue()});
 await p.evaluate(id=>{window.retry=app.retryNotification(id)},id);await arrived;await p.evaluate(id=>app.retryNotification(id),id);assert.equal(posts,1);release();await p.evaluate(()=>window.retry);await p.unrouteAll({behavior:'wait'});
 assert.equal((await f.api('GET','/api/notifications/'+id)).status,'pending');assert.equal(await p.getByRole('button',{name:'Повторить',exact:true}).count(),0);
 const result={mockProvider:'permanent_failure',initial:'failed',retry:'pending',postCount:posts,realSend:false};fs.writeFileSync(path.join(process.env.OUT,'results.json'),JSON.stringify(result,null,2));
 await c.close();await f.admin.close();await f.chair.close();
}finally{await b.close()}})().catch(e=>{console.error(e.stack);process.exitCode=1});
