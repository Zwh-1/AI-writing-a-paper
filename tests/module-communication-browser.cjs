// Consumer-level UI acceptance; a synthetic isolated execution boundary reads real inputs.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),out=path.join(root,'.runtime/module-communication-20261103');
const fixture=JSON.parse(fs.readFileSync(path.join(out,'fixture.json'),'utf8'));
const {chromium}=require(path.join(root,'.runtime/browser/node_modules/playwright'));
const checks=[],errors=[],failures=[];
async function main(){
 const candidates=fs.readdirSync(path.join(root,'.runtime/browsers')).filter(name=>name.startsWith('chromium')).flatMap(name=>['chrome-win64/chrome.exe','chrome-headless-shell-win64/chrome-headless-shell.exe'].map(tail=>path.join(root,'.runtime/browsers',name,tail)));
 const browser=await chromium.launch({headless:true,executablePath:candidates.find(name=>fs.existsSync(name))});
 const page=await browser.newPage({viewport:{width:1440,height:1050},locale:'zh-CN'});page.setDefaultTimeout(25000);
 const check=name=>{checks.push(name);console.log(name);};
 page.on('pageerror',error=>errors.push(error.message));
 page.on('response',response=>{if(response.url().includes('/api/')&&response.status()>=400)failures.push({path:new URL(response.url()).pathname,status:response.status()});});
 const products=async()=>{
  await page.getByRole('button',{name:'研究与协作',exact:true}).click();
  await page.getByRole('navigation',{name:'研究与协作工作区',exact:true}).getByRole('button',{name:/^产物与互通/}).click();
  await page.getByRole('heading',{name:'产物与互通',exact:true}).waitFor();
 };
 const pickNote=async()=>{
  await page.getByLabel('产物类型',{exact:true}).selectOption('note');
  const checkbox=page.getByLabel('选择产物：互通阅读随记',{exact:true});await checkbox.check();
  return page.locator('article.product-item').filter({has:checkbox});
 };
 try{
  const session=JSON.parse(fs.readFileSync(path.join(out,`session-${fixture.port}.json`),'utf8'));
  await page.goto(`http://127.0.0.1:${fixture.port}/#token=`+encodeURIComponent(session.bootstrap_token));
  await page.getByLabel('切换论文项目',{exact:true}).selectOption(fixture.project_id);
  await products();
  let card=await pickNote();
  await page.getByRole('button',{name:'存入共享资产库',exact:true}).click();
  await page.getByText('已保存到共享资产库，可在研究与成果、学术对话中使用。',{exact:true}).waitFor();
  await card.getByRole('button',{name:'查看流向',exact:true}).click();
  const flow=page.getByRole('region',{name:'产物来源与用途'});
  await flow.getByText('内容来源',{exact:true}).first().waitFor();
  await flow.getByText('互通阅读随记',{exact:true}).first().waitFor();
  check('a note-to-asset command appears in the actual downstream dataflow view');
  await page.getByLabel('产物目标章节',{exact:true}).selectOption(fixture.section_id);
  await page.getByRole('button',{name:'生成章节草稿',exact:true}).click();
  await page.getByRole('button',{name:'核对并采用草稿',exact:true}).click();
  await page.locator('.tiptap').filter({hasText:'应保留样本范围和适用条件。'}).first().waitFor();
  await page.getByRole('button',{name:'复用项目产物',exact:true}).click();
  card=await pickNote();await card.getByRole('button',{name:'查看流向',exact:true}).click();
  await page.getByRole('region',{name:'产物来源与用途'}).getByText('用于当前正文',{exact:true}).waitFor();
  check('an adopted draft is visible as a real current-document consumer');
  await page.getByRole('navigation',{name:'研究与协作工作区',exact:true}).getByRole('button',{name:/^研究助手/}).click();
  await page.getByRole('tab',{name:'学术对话',exact:true}).click();
  await page.getByRole('button',{name:'模块通信执行验收',exact:true}).click();
  const message=page.locator('.studio-message.assistant').filter({hasText:'可用以下代码读取所选产物：'});
  await message.getByText('选择执行输入产物（已选 0 项）',{exact:true}).click();
  await message.getByLabel('搜索对话产物',{exact:true}).fill('互通阅读随记');
  await message.locator('label').filter({hasText:'思维随记 · 互通阅读随记'}).getByRole('checkbox').check();
  await message.getByRole('button',{name:'运行 Python',exact:true}).click();
  await message.getByText('实际接收到产物：应保留样本范围和适用条件。',{exact:true}).waitFor();
  const input=JSON.parse(fs.readFileSync(path.join(out,'execution-input.json'),'utf8'));
  assert.equal(input.envelope.products[0].source_ref.id,fixture.note_id);
  assert.equal(input.envelope.products[0].text,'应保留样本范围和适用条件。');
  assert.ok(input.manifest.some(item=>item.name==='products.json'));
  check('the execution command passes the selected note content and version into products.json');
  await message.getByText('本次执行实际输入',{exact:true}).click();
  await message.getByText(/products.json ·/).waitFor();
  await message.getByRole('img',{name:'Python 生成的图像',exact:true}).waitFor();
  check('execution outputs expose their actual input manifest and reusable image asset');
  await products();card=await pickNote();await card.getByRole('button',{name:'查看流向',exact:true}).click();
  await page.getByRole('region',{name:'产物来源与用途'}).getByText('用于代码执行',{exact:true}).first().waitFor();
  check('the input note traces forward to the completed execution result');
  await page.getByLabel('产物类型',{exact:true}).selectOption('asset');
  const output=page.locator('article.product-item').filter({hasText:'模块执行产物.png'}).first();
  const relationsResponse=page.waitForResponse(response=>response.url().includes('/products/relations')&&response.request().method()==='POST');
  await output.getByRole('button',{name:'查看流向',exact:true}).click();
  assert.equal((await relationsResponse).status(),200);
  await page.getByRole('region',{name:'产物来源与用途'}).getByRole('heading',{name:'模块执行产物.png · 来源与用途',exact:true}).waitFor();
  await page.getByRole('region',{name:'产物来源与用途'}).getByText('正在读取流向…',{exact:true}).waitFor({state:'hidden'});
  await page.getByRole('region',{name:'产物来源与用途'}).getByRole('heading',{name:'内容来自哪里',exact:true}).waitFor();
  await page.getByRole('region',{name:'产物来源与用途'}).getByText('互通阅读随记',{exact:true}).first().waitFor();
  check('the execution-generated image traces back to its source note');
  await page.screenshot({path:path.join(out,'dataflow-desktop.png'),fullPage:true});
  await page.setViewportSize({width:430,height:932});
  assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+2));
  await page.screenshot({path:path.join(out,'dataflow-mobile.png'),fullPage:true});
  check('dataflow and execution interfaces fit the mobile viewport');
  assert.deepEqual(errors,[]);assert.deepEqual(failures,[]);
 }catch(error){
  await page.screenshot({path:path.join(out,'failure.png'),fullPage:true}).catch(()=>{});
  fs.writeFileSync(path.join(out,'failure.txt'),await page.locator('body').innerText().catch(()=>''));throw error;
 }finally{
  await browser.close();fs.writeFileSync(path.join(out,'module-browser-results.json'),JSON.stringify({checks,errors,failures},null,2));
 }
}
main().catch(error=>{console.error(error.stack);process.exitCode=1;});
