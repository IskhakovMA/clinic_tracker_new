// Public synthetic fixtures, always created through the real API on a fresh server.
const assert=require('node:assert/strict');
const password='Synthetic-WPH-Research-991!';
async function api(context,origin,method,url,data){const csrf=(await context.cookies()).find(x=>x.name==='clinic_csrf')?.value;const r=await context.request.fetch(origin+url,{method,data,headers:csrf?{'X-CSRF-Token':csrf}:{}});assert(r.ok(),`${method} ${url}: ${r.status()}`);return r.json()}
async function setup(browser,origin){const admin=await browser.newContext();const call=(method,url,data)=>api(admin,origin,method,url,data);await call('POST','/api/auth/login',{username:'wph.dev',password});const dept=await call('POST','/api/departments',{name:'Синтетическое I3'});const roles={developer:{username:'wph.dev'}};
for(const role of ['doctor','department_head','consilium_chair','deputy_chief_oncology','caop_head']){roles[role]=await call('POST','/api/users',{username:'i3.'+role,fullName:'Синтетический '+role,roleId:role==='consilium_chair'?'doctor':role,departmentIds:role==='doctor'||role==='department_head'?[dept.id]:[],password,mustChangePassword:false});}
const payload={name:'Синтетический Пациент I3',diagnosis:'C34.1',priority:'high',stage:'exam',head:'Синтетический Заведующий',nosology:'lung34',complaint:'Исходный комментарий',phone:'+70000000001',events:[{date:'2000-01-01',time:'10:00'},{date:'2000-01-02',time:'11:00'}]};
const card=await call('POST','/api/patients',{...payload,treatingDoctorUserId:roles.doctor.id});const chair=await call('POST','/api/patients',{...payload,name:'Синтетический Пациент Председателя I3',treatingDoctorUserId:roles.consilium_chair.id});await call('PATCH','/api/users/'+roles.consilium_chair.id,{roleId:'consilium_chair',departmentIds:[]});
let large=await call('POST','/api/patients',{...payload,name:'Синтетический '+('ДлинноеИмя'.repeat(16)),complaint:'Синтетический длинный комментарий. '.repeat(100),head:'Синтетический '+('Заведующий '.repeat(15)),treatingDoctorUserId:roles.doctor.id});
for(let i=0;i<30;i++){large=await call('POST',`/api/patients/${large.id}/stage`,{newStage:i%2?'exam':'appointment',expectedStage:large.stage,expectedHistoryEntryId:large.history.at(-1).id});}
return {admin,call,roles,card,chair,large,payload};}
module.exports={setup,api,password};
