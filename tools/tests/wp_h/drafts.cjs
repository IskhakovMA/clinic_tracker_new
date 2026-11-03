const {chromium}=require(process.env.PLAYWRIGHT_MODULE||'playwright'),assert=require('node:assert/strict');
(async()=>{
 const origin=process.env.WP_H_ORIGIN;assert.match(origin,/^http:\/\/127\.0\.0\.1:\d+$/);
 const browser=await chromium.launch({headless:true,executablePath:process.env.BROWSER_EXECUTABLE});
 try{
  const context=await browser.newContext(),page=await context.newPage();let writes=0;
  page.on('request',r=>{if(r.method()==='PATCH'&&new URL(r.url()).pathname.startsWith('/api/patients/'))writes++;});
  async function api(c,method,url,data){const csrf=(await c.cookies()).find(v=>v.name==='clinic_csrf')?.value;const r=await c.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),`${method} ${url} ${r.status()}`);return r.json();}
  await page.goto(origin);await page.bringToFront();await page.locator('#username').fill('wph.dev');await page.locator('#password').fill('Synthetic-WPH-Research-991!');await page.locator('#loginSubmit').click();await page.waitForFunction(()=>app.appReady);
  const userId=await page.evaluate(()=>app.user.id),id=await page.evaluate(()=>app.patientRows[0].id);
  await page.evaluate(id=>app.openPatient(id),id);const before=await page.evaluate(id=>app.getPatient(id),id);
  await api(context,'POST','/api/users',{username:'wph.admin2',fullName:'Другой Синтетический Администратор',roleId:'developer',password:'Synthetic-WPH-Research-991!',mustChangePassword:false,departmentIds:[]});
  const admin=await browser.newContext();await api(admin,'POST','/api/auth/login',{username:'wph.admin2',password:'Synthetic-WPH-Research-991!'});
  await page.getByRole('tab',{name:'Комментарии',exact:true}).click();const field=page.getByRole('textbox',{name:'Комментарии',exact:true});await field.fill('Синтетический несохраненный черновик');
  let releaseWrite,writeSeen;
  const heldWrite=new Promise(r=>{writeSeen=r;});
  await page.route('**/api/patients/'+id,async route=>{
   if(route.request().method()!=='PATCH')return route.continue();
   writeSeen();await new Promise(r=>{releaseWrite=r;});await route.continue();
  });
  await field.evaluate(e=>e.blur());await heldWrite;
  await api(admin,'POST','/api/users/'+userId+'/terminate-sessions',{});
  const expiredWrite=page.waitForResponse(r=>r.request().method()==='PATCH'&&new URL(r.url()).pathname==='/api/patients/'+id);
  releaseWrite();assert.equal((await expiredWrite).status(),401);await page.unroute('**/api/patients/'+id);
  await page.waitForFunction(()=>app.sessionExpired&&app.sessionFieldDrafts.size===1);
  await api(admin,'PATCH','/api/patients/'+id,{field:'complaint',value:'Синтетическое изменение другого сотрудника',expectedVersion:before.version});
  await page.locator('#password').fill('Synthetic-WPH-Research-991!');await page.locator('#loginSubmit').click();await page.waitForFunction(()=>!app.sessionExpired&&app.appReady);
  assert.equal(writes,1);
  await page.evaluate(()=>app.showView('patients'));
  await api(admin,'POST','/api/patients',{name:'Синтетический Новая ревизия',diagnosis:'C34.1',priority:'normal',stage:'exam',head:'Синтетический Заведующий'});
  await page.getByRole('button',{name:'Следующая страница',exact:true}).click();await page.waitForFunction(()=>app.patientPage.page.offset===0&&app.patientPage.page.total===101);
  const state=await page.evaluate(id=>({id:app.currentPatientId,draft:app.sessionFieldDrafts.get(id+':complaint'),server:app.getPatient(id)}),id);
  assert.equal(state.id,id);assert.equal(state.draft.body.expectedVersion,before.version);assert.equal(state.draft.body.value,'Синтетический несохраненный черновик');assert.equal(state.server.complaint,'Синтетическое изменение другого сотрудника');assert.equal(writes,1);
  await api(admin,'POST','/synthetic/wph/release-race',{mode:'once'});
  await page.evaluate(()=>app.reloadPatientsAndRender());
  const retained=await page.evaluate(id=>({id:app.currentPatientId,draft:app.sessionFieldDrafts.get(id+':complaint')}),id);
  assert.equal(retained.id,id);assert.equal(retained.draft.body.expectedVersion,before.version);assert.equal(retained.draft.body.value,'Синтетический несохраненный черновик');assert.equal(writes,1);
  await page.evaluate(id=>app.openPatient(id),id);await page.getByRole('tab',{name:'Комментарии',exact:true}).click();
  const response=page.waitForResponse(r=>r.request().method()==='PATCH'&&new URL(r.url()).pathname==='/api/patients/'+id);
  await page.getByRole('button',{name:'Сохранить правку',exact:true}).click();const denied=await response;
  assert.equal(denied.status(),409);assert.equal(denied.request().postDataJSON().expectedVersion,before.version);
  assert.equal((await api(admin,'GET','/api/patients/'+id)).complaint,'Синтетическое изменение другого сотрудника');assert.equal(writes,2);
  assert.equal(await page.evaluate(()=>app.sessionFieldDrafts.size),1);
  console.log(JSON.stringify({realBackend:true,draftAcross401AndPage409:true,selectedPatientRetained:true,originalVersionRetained:true,noAutomaticWrite:true,manualStaleSave409:true,serverNotOverwritten:true}));
  await admin.close();await context.close();
 }finally{await browser.close();}
})().catch(e=>{console.error(e.stack);process.exitCode=1;});
