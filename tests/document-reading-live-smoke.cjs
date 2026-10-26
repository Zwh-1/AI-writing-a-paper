const fs=require('fs'),path=require('path'),assert=require('node:assert/strict');
const {chromium}=require(process.env.PLAYWRIGHT_MODULE || 'C:/Users/abcta/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const root=path.resolve(__dirname,'..'),origin='http://127.0.0.1:8765';
(async()=>{
  const browser=await chromium.launch({headless:true,executablePath:path.join(root,'.runtime/browsers/chromium-1234/chrome-win64/chrome.exe')});
  try {
    const context=await browser.newContext({viewport:{width:1440,height:1000},locale:'zh-CN'});
    const token=JSON.parse(fs.readFileSync(path.join(root,'.runtime/session-8765.json'),'utf8')).bootstrap_token;
    const session=await context.request.post(origin+'/api/v1/session',{data:{token}});assert.equal(session.status(),200);
    const auth=await session.json();const health=await (await context.request.get(origin+'/api/v1/health')).json();assert.equal(health.version,'0.6.1');
    const projects=await (await context.request.get(origin+'/api/v1/projects')).json();const pid=projects.find(p=>!p.archived).id;
    const base=`${origin}/api/v1/projects/${pid}`,reading=base+'/document-reading';
    const research=await (await context.request.get(reading+'/documents')).json();assert.equal(research.total,31);
    const sbt=research.documents.find(f=>f.file_id==='a9065698848a4300a33c6f04f37ef68c');assert.ok(sbt);assert.equal(sbt.primary_role,'example');assert.deepEqual(sbt.usages,['example','research']);
    const guidance=await (await context.request.get(reading+'/documents?intent=guidance')).json();assert.ok(guidance.documents.find(f=>f.file_id===sbt.file_id));
    assert.ok(!research.documents.find(f=>f.file_id==='4682795ddce646d4897bbf922298cee1'));
    const directory=await (await context.request.get(reading+'/documents/a346aa5f45de4ec1a455ccad22d35db2/outline?intent=guidance')).json();
    const native=directory.units.find(u=>u.origin==='pptx_slide'&&u.locator.slide_number===6);assert.ok(native);
    const grade=guidance.documents.find(f=>f.file_id==='4682795ddce646d4897bbf922298cee1');assert.equal(grade.material_use.source_kind,'grading_criteria');assert.ok(grade.usages.includes('grading'));
    const response=await context.request.post(base+'/evidence-knowledge/search',{headers:{'X-CSRF-Token':auth.csrf},data:{query:'SBTCert Soulbound',limit:8},timeout:90000});assert.equal(response.status(),200);
    const found=await response.json();assert.ok(found.items.some(i=>i.file_id===sbt.file_id));
    const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
    await page.addInitScript(id=>localStorage.setItem('paper_project',id),pid);await page.goto(origin);await page.getByRole('button',{name:'文献与术语',exact:true}).click();
    const panel=page.locator('section[aria-label="文献与资料阅读"]');await panel.getByRole('heading',{name:'文献与资料阅读',exact:true}).waitFor();
    await panel.getByLabel('查找阅读文献',{exact:true}).fill('SBTCert');await panel.locator('.dr-documents article').filter({hasText:'SBTCert'}).waitFor();
    await page.screenshot({path:path.join(root,'.runtime/document-reading-live.png')});assert.deepEqual(errors,[]);
    const report={version:health.version,research_documents:research.total,guidance_file_records:guidance.total,sbtcert_primary_role:sbt.primary_role,sbtcert_uses:sbt.usages,teaching_slide_6_preserved:!!native,grading_is_guidance:grade.material_use.source_kind,actual_sbtcert_retrieved:true,reading_records_created:0,paid_generation_requested:false,errors};
    fs.writeFileSync(path.join(root,'.runtime/document-reading-live-smoke.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
  }finally{await browser.close();}
})().catch(e=>{console.error(e);process.exitCode=1;});
