'use strict';
/* Fictional in-memory fixtures only; no real CRM/company/owner/runtime data. */
const test=require('node:test'),assert=require('node:assert/strict'),fs=require('node:fs'),path=require('node:path'),vm=require('node:vm');
const html=fs.readFileSync(path.join(__dirname,'..','index.html'),'utf8');
function fixture(actor='MIRA'){
 const company={id:'fictional-company',name:'Fictional test company',domain:'fictional.example',sector:'Fictional sector',evidence:'Fictional source'};
 const opportunity={id:'fictional-opportunity',company_id:company.id,title:'Fictional test opportunity',stage:'Qualified',technical_scope:'Fictional scope; no client system',milestones:'Fictional acceptance',uncertainty:'Budget and interest unknown'};
 const decision={schema:'crm-reevaluation-v1',task_id:'fictional-review-round',decision:'retain',score:80,components:{capacity:20,need:20,access:15,value:15,urgency:10},mandatory_gates_passed:true,summary:'Fictional evidence-backed hypothesis',service:'Fictional bounded service',opportunity:{what_to_build:'Fictional scoped demo',growth_mechanism:'Fictional retention hypothesis',assumptions:'Unconfirmed budget and interest'},contact:'https://fictional.example/contact',sources:['https://fictional.example/source'],evidence_date:'2026-01-01',next_action:'Review evidence',owner:'MIRA',follow_up:null,agent_work:[],reasons:[]};
 const note={id:'fictional-audit-note',opportunity_id:opportunity.id,kind:'crm-reevaluation',body:JSON.stringify(decision),evidence:'Fictional review',created_at:'2026-01-02T00:00:00Z'};
 const event={id:'fictional-event',actor,operation:'note.create',record_id:note.id,after_json:JSON.stringify(note)};
 return {data:{companies:[company],opportunities:[opportunity],notes:[note],activity:[event],drafts:[]},decision,note,event};
}
function api(){
 const segment=html.slice(html.indexOf('const crmState={'),html.indexOf('function crmPage(){'));
 const context=vm.createContext({URL,URLSearchParams,console,Date,JSON,localStorage:{getItem:()=>null}});
 vm.runInContext(segment+'\nglobalThis.api={project:typeof crmAuditProject==="function"?crmAuditProject:null,card:typeof crmAuditCard==="function"?crmAuditCard:null};',context);
 return context.api;
}
function refresh(f){f.note.body=JSON.stringify(f.decision);f.event.after_json=JSON.stringify(f.note);return f;}

test('Legacy Qualified stage alone is not verified retention',()=>{
 const a=api();assert.equal(typeof a.project,'function','Missing current audited CRM projection');
 const f=fixture();f.data.notes=[];f.data.activity=[];
 const p=a.project(f.data);assert.equal(p.active.length,0);assert.equal(p.review.length,1);
});

test('Own audit round retains only exact authorized coordinator/commercial notes',()=>{
 const a=api();
 for(const actor of ['MIRA','SURGE']){const f=fixture(actor);assert.equal(a.project(f.data).active.length,1);}
 for(const actor of ['SAGE','SPECTRA','SELINE']){const f=fixture(actor);assert.equal(a.project(f.data).active.length,0);}
 const forged=fixture();forged.event.after_json='{}';assert.equal(a.project(forged.data).active.length,0);
});

test('Score/all-gates/opportunity/source/contact negatives fail closed',()=>{
 const a=api();
 for(const change of [p=>p.score=79,p=>p.score=101,p=>p.score=true,p=>p.mandatory_gates_passed=false,p=>p.opportunity='Unknown',p=>p.contact='javascript:alert(1)',p=>p.sources=[],p=>p.components.capacity=25,p=>delete p.summary]){
  const f=fixture();change(f.decision);refresh(f);assert.equal(a.project(f.data).active.length,0);
 }
});

test('New malformed or future notes and source changes cannot reuse old retention',()=>{
 const a=api(),f=fixture();
 const note={...f.note,id:'fictional-newer-note',created_at:'2026-01-03T00:00:00Z',body:'{malformed'};
 f.data.notes.push(note);f.data.activity.push({...f.event,record_id:note.id,after_json:JSON.stringify(note)});
 assert.equal(a.project(f.data).active.length,0);
 const future=fixture();future.note.created_at='2099-01-01T00:00:00Z';refresh(future);assert.equal(a.project(future.data).active.length,0);
 const stale=fixture();stale.data.activity.push({operation:'company.update',record_id:'fictional-company',created_at:'2026-01-03T00:00:00Z',before_json:'{"evidence":"old"}',after_json:'{"evidence":"changed"}'});
 assert.equal(a.project(stale.data).active.length,0);
});

test('Discovery/rejected/inactive records stay separate with original IDs',()=>{
 const a=api(),f=fixture();f.decision.decision='discovery';f.decision.score=65;refresh(f);
 assert.equal(a.project(f.data).active.length,0);assert.equal(a.project(f.data).review[0].opportunity.id,'fictional-opportunity');
 f.decision.decision='reject';f.decision.reasons=['Fictional capacity evidence missing'];refresh(f);assert.equal(a.project(f.data).rejected.length,1);
 const inactive=fixture();inactive.data.opportunities[0].stage='Disqualified';assert.equal(a.project(inactive.data).active.length,0);assert.equal(a.project(inactive.data).rejected.length,1);
});

test('Readable cards escape untrusted input and retain full scope under details',()=>{
 const a=api(),f=fixture();f.decision.summary='<img src=x onerror=alert(1)> Fictional hypothesis';f.data.opportunities[0].technical_scope='Fictional reviewed scope '.repeat(50);refresh(f);
 const card=a.card(a.project(f.data).active[0],f.data);
 assert.ok(card.includes('&lt;img'));assert.ok(!card.includes('<img'));assert.ok(!card.includes('[object Object]'));
 assert.ok(!card.split('<details>')[0].includes(f.data.opportunities[0].technical_scope.trim()));assert.ok(card.includes(f.data.opportunities[0].technical_scope.trim()));
});

test('Portable own-instance document references use agent/filename, not host paths',()=>{
 const a=api(),f=fixture();f.decision.agent_work=[{agent:'SPECTRA',filename:'fictional-demo.md',summary:'Fictional artifact'}];refresh(f);
 assert.ok(a.card(a.project(f.data).active[0],f.data).includes('/api/content/download?agent=SPECTRA&amp;file=fictional-demo.md'));
 f.decision.agent_work=[{agent:'SPECTRA',filename:'../escape.md',url:'javascript:alert(1)'}];refresh(f);
 const card=a.card(a.project(f.data).active[0],f.data);assert.ok(!card.includes('href="javascript:'));assert.ok(!card.includes('file=..'));
});

test('Projection and cards never mutate source records or grant sending authority',()=>{
 const a=api(),f=fixture(),before=JSON.stringify(f.data),p=a.project(f.data),card=a.card(p.active[0],f.data);
 assert.equal(JSON.stringify(f.data),before);assert.ok(card.includes('not buyer interest or sending approval'));
 assert.ok(card.includes('not a production-ready')||card.includes('production-ready client solution'));
});
