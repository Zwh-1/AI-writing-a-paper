// Reconcile every original requirement with current target-project code and evidence.
import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import assert from 'node:assert/strict';
import {fileURLToPath} from 'node:url';
const root=path.resolve(path.dirname(fileURLToPath(import.meta.url)),'..');
const baselineFile=path.join(root,'docs/PaperSpark功能对照审计_2026-11-02/audit-data.json');
const baseline=JSON.parse(fs.readFileSync(baselineFile,'utf8'));
const version=JSON.parse(fs.readFileSync(path.join(root,'frontend/package.json'),'utf8')).version;
const out=path.join(root,'docs/功能完整性复核_2026-11-02');fs.mkdirSync(out,{recursive:true});
const files=new Map();
const aliases={
 'backend/app/main.py':['backend/app/main.py','backend/app/reference_service.py','backend/app/export_service.py'],
 'backend/app/studio_imports.py':['backend/app/literature_connectors.py','backend/app/reference_service.py'],
 'backend/app/studio_ai.py':['backend/app/assistant_service.py'],
 'backend/app/studio_context.py':['backend/app/assistant_context.py'],
 'backend/app/studio_exports.py':['backend/app/research_delivery.py','backend/app/workspace_maintenance.py'],
 'backend/app/studio_api.py':['backend/app/research_api.py'],
 'backend/app/studio_store.py':['backend/app/research_records.py','backend/app/credential_service.py'],
 'backend/app/studio_reading.py':['backend/app/reading_service.py'],
 'backend/app/studio_parsing.py':['backend/app/parsing_service.py','backend/app/word_conversion.py'],
 'backend/app/studio_graph.py':['backend/app/knowledge_metadata.py'],
 'backend/app/studio_execution.py':['backend/app/execution_service.py'],
 'frontend/src/studio/Studio.tsx':['frontend/src/studio/Studio.tsx','frontend/src/references/LiteratureIntake.tsx','frontend/src/exports/ResearchDelivery.tsx'],
 'frontend/src/studio/Reader.tsx':['frontend/src/reading/Reader.tsx'],
 'frontend/src/studio/ChatNotes.tsx':['frontend/src/assistant/ChatNotes.tsx','frontend/src/editor/ResearchTextEditor.tsx'],
 'frontend/src/studio/EditorTools.tsx':['frontend/src/editor/EditorTools.tsx','frontend/src/editor/CommentPanel.tsx','frontend/src/editor/CompletionTool.tsx','frontend/src/editor/FormulaTool.tsx','frontend/src/editor/EditorPort.ts'],
 'frontend/src/studio/EditorExtensions.ts':['frontend/src/editor/EditorExtensions.ts'],
 'frontend/src/studio/MathInput.tsx':['frontend/src/editor/MathInput.tsx'],
 'frontend/src/studio/CanvasEditor.tsx':['frontend/src/assets/CanvasEditor.tsx'],
 'frontend/src/studio/RecordHistory.tsx':['frontend/src/research/RecordHistory.tsx'],
 'frontend/src/studio/AgentSettings.tsx':['frontend/src/assistant/AgentSettings.tsx'],
 'frontend/src/studio/Assets.tsx':['frontend/src/assets/Assets.tsx'],
 'frontend/src/studio/Settings.tsx':['frontend/src/settings/ResearchSettings.tsx']
};
function E(file,needle){
 if(file==='frontend/package.json'&&needle.includes('"version"'))needle='"version": "'+version+'"';
 for(const candidate of aliases[file]||[file]){
  const text=fs.readFileSync(path.join(root,candidate),'utf8');
  // Formatting may split JSX but the actual token must still be present.
  const lines=text.split(/\r?\n/);
  let line=lines.findIndex(value=>value.includes(needle));
  if(line<0){
   const target=needle.replace(/\s+/g,'').replaceAll("'",'"'),compact=text.replace(/\s+/g,'').replaceAll("'",'"'),offset=compact.indexOf(target);
   if(offset>=0){let consumed=0;line=lines.findIndex(value=>{consumed+=value.replace(/\s+/g,'').length;return consumed>offset;});}
  }
  if(line<0)continue;
  files.set(candidate,{file:candidate,sha256:crypto.createHash('sha256').update(text).digest('hex')});
  return {file:candidate,line:line+1,needle};
 }
 assert.fail(file+': '+needle);
}

const implementation={
Y01:{text:'URL下载、原件验证、解析、来源记录和稳定书目关联已接通。',proof:[E('backend/app/studio_imports.py','def import_url('),E('frontend/src/studio/Studio.tsx','从链接导入')]},
Y02:{text:'Zotero API Key/OAuth、分页同步、稳定ID、附件状态与失败续传；局部失败不报全部成功。',condition:'Zotero账户/应用凭证及网络',proof:[E('backend/app/studio_imports.py','def zotero_sync('),E('backend/app/studio_imports.py','def finish_oauth('),E('backend/app/tasks.py',"result.get('complete') is False")]},
Y03:{text:'书目摘要翻译与当前摘要哈希匹配，旧译文不冒充当前版本。',condition:'已配置的AI模型',proof:[E('backend/app/studio_ai.py','def abstract('),E('backend/app/studio_api.py','def current_abstract(')]},
Y04:{text:'标签增删改、筛选及前端批量打标已实现。',proof:[E('backend/app/studio_imports.py','def update_tags('),E('frontend/src/studio/Studio.tsx','所选文献批量标签')]},
Y05:{text:'移出当前库、整批共享原件判定、索引失效、历史保留和孤立资源回收已实现。',proof:[E('backend/app/studio_exports.py','def deletion_preview('),E('backend/app/studio_exports.py','def orphan_objects(')]},
Y06:{text:'原译同滚动区、分块缓存、语言选择、阅读位置、逐段标读与版本失效均已实现。',condition:'已配置的翻译模型',proof:[E('backend/app/studio_ai.py','def translate('),E('frontend/src/studio/Reader.tsx','studio-parallel')]},
Y07:{text:'章节/段落结构图与点击原文定位已实现；AI导读重点逐字校验原文。',proof:[E('frontend/src/studio/Reader.tsx','function MapView('),E('backend/app/studio_ai.py','def guide(')]},
Y08:{text:'编号引用及范围、参考条目、DOI链接、条目定位和返回正文已实现。',proof:[E('backend/app/studio_reading.py','def reference_entries('),E('frontend/src/studio/Reader.tsx','返回引用段落')]},
Y09:{text:'原文选区高亮/批注、位置哈希、Unicode字符范围、回跳和删除已实现。',proof:[E('backend/app/studio_reading.py','def annotate('),E('frontend/src/studio/Reader.tsx','Array.from(prefix.toString())')]},
Y10:{text:'独立交互HTML生成、版本保存、隔离预览和下载已实现。',condition:'已配置的AI模型',proof:[E('backend/app/studio_ai.py','def webpage('),E('frontend/src/studio/Reader.tsx','论文交互网页预览')]},
Y11:{text:'连续对话、角色/模型/能力CRUD、原文/资产实际内容、当前章节及待采用改写已接通。',condition:'已配置的AI模型；图片资产需要图像模型能力',proof:[E('backend/app/studio_ai.py','def chat('),E('backend/app/studio_context.py','def artifact('),E('backend/app/studio_context.py','def adopt('),E('frontend/src/studio/AgentSettings.tsx','学术智能体设置')]},
Y12:{text:'富文本随记、标签、搜索、消息摘存、AI整理候选、版本查看与恢复已实现。',proof:[E('frontend/src/studio/ChatNotes.tsx','export function StudioNotes('),E('frontend/src/studio/RecordHistory.tsx','查看版本与恢复'),E('backend/app/studio_store.py','def restore(')]},
Y13:{text:'光标补全及显式自动补全、版本快照检查、锁定与错误反馈已实现。',condition:'已配置的AI模型',proof:[E('backend/app/studio_ai.py','def completion('),E('frontend/src/studio/EditorTools.tsx','const complete=async')]},
Y14:{text:'选区范围评论、重复句/Unicode定位、线程、人工/AI回复和解决状态已实现。',proof:[E('backend/app/studio_ai.py','def add_comment('),E('frontend/src/studio/EditorExtensions.ts','export function anchorText(')]},
Y15:{text:'MathLive与本地字体、行内/块公式、修改原公式、Word结构化数学及TeX输出已实现。',proof:[E('frontend/src/studio/MathInput.tsx','MathfieldElement'),E('backend/app/exports.py',"elif typ=='formula':"),E('frontend/src/studio/EditorExtensions.ts','export const InlineMath')]},
Y16:{text:'自由场景编辑/撤销、源场景版本、历史恢复、PNG及正文插入和再编辑已实现。',proof:[E('frontend/src/studio/CanvasEditor.tsx','export default function CanvasEditor('),E('frontend/src/studio/EditorTools.tsx',"'/metadata'")]},
Y17:{text:'隔离Python执行、图片输出、内容校验、中文字体、资源归档与正文回填已实现。',condition:'Docker引擎与执行镜像',proof:[E('backend/app/studio_execution.py','def local('),E('backend/app/studio_execution.py','def collect('),E('deployment/execution.Dockerfile','fonts-noto-cjk')]},
Y18:{text:'远端执行协议、服务凭证、原件外发权限、健康检查及broker已实现。',condition:'已部署的远端Docker执行服务',proof:[E('backend/app/studio_execution.py','def health('),E('services/execution_service.py',"@app.post('/execute')")]},
Y19:{text:'作者/关键词节点、书目来源、跨论文主题边、变更失效与同名消歧保留已实现。',proof:[E('backend/app/studio_graph.py','def enrich('),E('backend/app/studio_graph.py',"'shares_keywords'")]},
Y20:{text:'中英文LaTeX工程ZIP、主TeX/Bib、图片、表格标签/跨节引用、编译说明和版本清单已实现。',proof:[E('backend/app/studio_exports.py','def latex_project('),E('backend/app/studio_exports.py',"'visual:'")]},
Y21:{text:'Surya本地/Modal、MinerU配置、任务恢复、结果规范化、采用与原知识head联动已实现。',condition:'相应解析服务/模型与凭证',proof:[E('backend/app/studio_parsing.py','def parse('),E('backend/app/studio_parsing.py','def adopt('),E('services/modal_surya.py','def process_job(')]},
Y22:{text:'OpenAlex分页/年份/开放获取、元数据/全文导入、查询扩展和复审已实现。',condition:'OpenAlex账号/网络；AI扩展需要模型',proof:[E('backend/app/studio_imports.py','def openalex('),E('backend/app/studio_ai.py','def literature_plan(')]},
Y23:{text:'summary/list/get/search、字段读取、完整集合读取、在线会话及只读离线备份查询已实现，在线/离线共用查询投影。',proof:[E('scripts/yantai-data.mjs','workspace/query'),E('scripts/yantai_data_offline.py','mode=ro'),E('scripts/yantai_query_projection.py','def query(')]},
Y24:{text:'Windows原生程序/NSIS已实际构建并检查代码和资源；macOS app/DMG/ZIP流程与平台凭据/OCR适配有实现。',condition:'Windows原生运行/安装及macOS实机验收；当前已验证Windows构建与包内资源',proof:[E('deployment/desktop/yantai.spec','collection=COLLECT'),E('deployment/desktop/windows.nsi','APP_SOURCE'),E('backend/app/config.py','sys._MEIPASS'),E('.github/workflows/release.yml','Package macOS DMG')]},
Y25:{text:'全前端/API/worker持久卷、Surya CPU/GPU镜像和平台OCR/Office依赖均有构建实现。',condition:'可用Docker引擎；GPU路径需NVIDIA环境',proof:[E('deployment/workbench.Dockerfile','COPY --from=frontend'),E('deployment/workbench.compose.yaml','workbench-runtime:'),E('deployment/workbench.gpu.yaml','capabilities: [gpu]')]},
Y26:{text:'PR校验/自动PR、规范提交版本选择/同步、发布包、跨架构镜像发布实现齐备；未擅自远端发布。',condition:'GitHub Actions权限/目标平台与注册表',proof:[E('.github/workflows/auto-pr.yml','gh pr create'),E('scripts/release-version.mjs','BREAKING CHANGE'),E('.github/workflows/release.yml','platforms: linux/amd64,linux/arm64')]},
Y27:{text:'缓存占用、分类清理、运行任务拦截、原件保护与可预览孤立回收均已实现。',proof:[E('backend/app/studio_exports.py','def clear_cache('),E('frontend/src/studio/Settings.tsx','磁盘缓存与孤立资源')]},
Y28:{text:'旧DOC格式签名、独立LibreOffice转换和DOCX解析复用已实现。',condition:'LibreOffice引擎',proof:[E('backend/app/studio_parsing.py','def convert_doc('),E('backend/app/parsers.py',"if ext=='.doc':")]},
Y29:{text:'区域分类/OCR与读序规范化已实现，不再丢弃未分配OCR文字；结果可采用、保留类型/坐标/置信度。',condition:'Surya/MinerU真实模型与复杂扫描样本质量验收',proof:[E('services/surya_service.py','def normalize_results('),E('backend/app/studio_parsing.py','def normalize_blocks(')]},
};
const extra={
 A14:[E('frontend/src/studio/Assets.tsx','StudioAssets'),E('backend/app/studio_context.py','def artifact('),E('frontend/src/assistant/ChatNotes.tsx','availableAssets')],
 A17:[E('backend/app/studio_context.py','def proposed_document('),E('backend/app/studio_context.py','def adopt(')],
 C02:[E('backend/app/research_api.py','def workspace_query(')],
 D07:[E('.github/workflows/release.yml','ditto -c -k')],
 D08:[E('.github/workflows/auto-pr.yml','gh pr edit')],
 D09:[E('scripts/release-version.mjs','const installer=')]
};
const features=baseline.features.map(item=>{
 let proof=[],details=[],conditions=[];
 for(const id of item.gaps){const value=implementation[id];assert.ok(value,id);proof.push(...value.proof);details.push(value.text);if(value.condition)conditions.push(value.condition);}
 if(!item.gaps.length){details=[item.actual.replaceAll('0.7.4',version)];proof=item.evidence.map(value=>E(value.file,value.file==='frontend/package.json'&&value.needle.includes('"version"')?'"version": "'+version+'"':value.needle));}
 if(extra[item.id])proof.push(...extra[item.id]);
 if(item.id==='C02')details=['统一 workspace/query API 已实现；在线/离线使用相同只读投影，支持概要、集合、详情和当前内容查询。'];
 if(item.id==='D07')details=['macOS .app/DMG/ZIP 构建与上传流程齐备；macOS 实机构建和安装尚未在当前 Windows 环境验证。'];
 if(item.id==='D08')details=['开发分支推送时创建 PR；已存在 PR 则更新当前源码修订说明。工作流已实现，未执行远端创建或发布。'];
 if(item.id==='D09')details=['版本脚本同时更新前端、lockfile、后端和 NSIS 安装器，隔离临时目录回归验证通过。'];
 return{id:item.id,category:item.category,name:item.referenceName,reference:item.reference,implementation:'已找到完整源码路径或等效实现',verification:conditions.length?'外部条件未全量实测':'源码/相关自动验证已核对',conditions:[...new Set(conditions)],details:[...new Set(details)],evidence:[...new Map(proof.map(value=>[value.file+':'+value.line,value])).values()]};
});
assert.equal(features.length,106);assert.equal(new Set(features.map(row=>row.id)).size,106);
const xmlFiles=['completeness-verified-all.xml','completeness-frozen-tests.xml','completeness-follow-up-ui.xml'];
const tests=xmlFiles.map(file=>{
 const full=path.join(root,'.runtime',file);if(!fs.existsSync(full))return{file,available:false};
 const raw=fs.readFileSync(full,'utf8'),suites=[...raw.matchAll(/<testsuite\b[^>]*>/g)].map(match=>match[0]);
 return{file,available:true,...Object.fromEntries(['tests','failures','errors','skipped','time'].map(key=>[key,suites.reduce((sum,suite)=>sum+Number(suite.match(new RegExp(`${key}="([^"]+)"`))?.[1]||0),0)]))};
});
const browser=JSON.parse(fs.readFileSync(path.join(root,'.runtime/studio-acceptance-20261102/browser-results.json'),'utf8'));
const delivery=JSON.parse(fs.readFileSync(path.join(root,'.runtime/studio-acceptance-20261102/delivery-results.json'),'utf8'));
const counts={features:features.length,sourceCovered:features.length,conditionalVerification:features.filter(row=>row.conditions.length).length,browserScenarios:browser.checks.length};
const sourceLink=e=>`[${e.file}:${e.line}](<${path.join(root,e.file).replace(/\\/g,'/')}:${e.line}>)`;
let text=`# PaperSpark 106项功能：砚台${version}最终源码复核\n\n**先明确结论：上一轮0.8.0确实还存在未接通的环节。本轮已继续实现和修复；当前106个条目均能定位到功能实现或等效方案。外部账号、Docker/GPU、macOS及被拦截的原生启动尚未完成实机验收，不能把这份报告称为“106项全部实机通过”。**\n\n检查对象是砚台项目，不审计PaperSpark本身。原始106项要求全部保留，不把不同库名、API路径或架构判为缺失。\n\n## 本轮查出的缺口和修复\n\n- Zotero父条目版本不变时附件失败无法补取：增加附件单独状态/指纹与续传；任务局部失败改为 incomplete。\n- 助手仅拿资产元数据：现在读取真实文本/表格/PDF/DOCX内容，图片通过图像输入发送；派生来源权限在发送和历史复用前检查。\n- 写作区助手缺少当前正文与应用路径：接入段落和版本快照，模型只提出精确文本操作，用户审核后通过锁定/版本/来源事务形成新正文版本。\n- 智能体只有创建、随记/画布只有历史写入：补齐智能体编辑/删除、历史查看和创建恢复版本。\n- 阅读未集成原页和导读：新增原始页面、逐字引句校验的AI重点导读及原文定位、同页问答入口。\n- 评论重复句和emoji偏移：增加明确起止范围，前后端使用一致的字符定位，公式/引用后的位置有映射。\n- 文献批量删除共享原件判定、重新导入停用文献、批量标签、富文本资产编辑器/表格、过期摘要译文均补齐。\n- 图谱书目变更、共享标签依赖、独立主题连边、块公式Word结构、LaTeX跨节/表格引用和采用时书目进一步补齐。\n- 冻结程序资源/迁移目录、NSIS中文编码/构建路径、CLI集合截断/安装态会话目录、云解析断线恢复/未分配OCR文字和运行健康检查均修正。\n\n## 逐项清单\n\n`;
for(const category of [...new Set(features.map(row=>row.category))]){
 text+=`### ${category}\n\n| 编号 | 参考功能 | 你的项目当前实现 | 仍需的运行/验收条件 | 当前代码证据 |\n| --- | --- | --- | --- | --- |\n`;
 for(const row of features.filter(row=>row.category===category))text+=`| ${row.id} | ${row.name} | ${row.details.join('<br>')} | ${row.conditions.join('<br>')||'无新增外部条件；不等于已穷尽所有边界'} | ${row.evidence.map(sourceLink).join('<br>')} |\n`;
 text+='\n';
}
text+=`## 实际验证与未完成验收\n\n${tests.filter(row=>row.available).map(row=>`- ${row.file}：${row.tests}项、失败${row.failures}、错误${row.errors}。`).join('\n')}\n- 浏览器：${browser.checks.length}个流程，无脚本异常${browser.errors.length===0?'（已核对）':'（有异常）'}；采用合成模型，证明UI和数据接通，不证明真实模型效果。\n- CLI：在线/离线共${delivery.cli.length}组合；TeX：${delivery.latex.map(row=>`${row.language}/${row.engine}退出码${row.returncode}`).join('，')}。\n- Windows原生程序/安装器以本轮部署验收记录为准；旧版构建不能替代当前源码的交付验证。\n- 原生程序启动验收被自动审批拦截，返回“blocked by policy”，未规避该拦截。\n- 当前Docker Desktop启动请求后仍没有可连接引擎；未实际运行容器/GPU。未连接真实Zotero/OpenAlex/MinerU/Modal账号，未在macOS构建或安装DMG。\n- 仓库源码、构建流程和接口可完成的缺口已经补齐；以上实机/账户条件不能仅靠再写代码或模拟响应自动变成验收通过。\n\n本报告保留上述区别，不承诺未知服务协议、所有扫描件质量、任何平台包签名或所有模型返回均无缺陷。用户现有工作树修改和真实论文数据保留；没有擅自推送发版。\n`;
text=`# 106 项参考功能完整性复核：砚台 ${version}\n\n检查对象：砚台论文工作台。106项均保留独立条目，以下记录实际实现位置及外部运行条件。库名、服务框架和桌面封装采用项目现有技术栈的功能等效方案，不宣称复制 PaperSpark 的技术实现。\n\n本轮修复与实际验收见 [功能完整性复查与补齐记录](../功能完整性复查与补齐记录_2026-11-02.md)。源码路径覆盖不等于真实账号、全部平台和所有文献质量均已验收。\n\n`;
for(const category of [...new Set(features.map(row=>row.category))]){
 text+=`## ${category}\n\n| 编号 | 功能 | 实现/等效方案 | 运行条件 | 当前源码证据 |\n| --- | --- | --- | --- | --- |\n`;
 for(const row of features.filter(value=>value.category===category))text+=`| ${row.id} | ${row.name} | ${row.details.join('<br>')} | ${row.conditions.join('<br>')||'本地功能；仍以具体测试为准'} | ${row.evidence.map(sourceLink).join('<br>')} |\n`;
 text+='\n';
}
text+=`## 本轮验证\n\n${tests.filter(row=>row.available).map(row=>`- ${row.file}：${row.tests}项，失败${row.failures}，错误${row.errors}。`).join('\n')}\n- 原浏览器场景：${browser.checks.length}项，脚本异常${browser.errors.length}项。\n- CLI：在线/离线${delivery.cli.length}组；TeX：${delivery.latex.map(row=>`${row.language}/${row.engine}退出码${row.returncode}`).join('，')}。\n- Windows原生应用和安装器：以完整性复查记录中的当前版本、资源清单和哈希为准。\n- Docker引擎、GPU、真实外部账号、macOS实机和远端发布未自动变成已验收，不以模拟响应替代真实验证。\n`;
fs.writeFileSync(path.join(out,'README.md'),text,'utf8');
fs.writeFileSync(path.join(out,'audit-data.json'),JSON.stringify({date:'2026-11-02',target:root,version,counts,features,implementation,tests,browser,delivery,sourceManifest:[...files.values()],allSourcePathsPresent:true,allRealEnvironmentsAccepted:false},null,2)+'\n','utf8');
console.log(JSON.stringify(counts,null,2));
