// Exercise the real Tiptap schema's JSON load/save path without a browser.
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const {createRequire} = require('node:module');

const root=path.resolve(__dirname,'..');
const req=createRequire(path.join(root,'frontend','package.json'));
const ts=req('typescript');
const source=fs.readFileSync(path.join(root,'frontend/src/editor/ResearchContentExtensions.ts'),'utf8');
const compiled=ts.transpileModule(source,{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const mod={exports:{}};
const run=vm.runInThisContext('(function(require,module,exports){'+compiled+'\n})');
run((name)=>name==='../api/client'?{apiUrl:value=>value}:req(name),mod,mod.exports);
const {getSchema}=req('@tiptap/core');
const starter=req('@tiptap/starter-kit').default;
const {Table}=req('@tiptap/extension-table');
const cell=req('@tiptap/extension-table-cell').default;
const header=req('@tiptap/extension-table-header').default;
const row=req('@tiptap/extension-table-row').default;
const schema=getSchema([starter,Table,row,cell,header,mod.exports.ResearchAttributes,...mod.exports.researchAtoms()]);
const paragraph=text=>({type:'paragraph',content:[{type:'text',text}]});
const original={type:'doc',content:[
  {type:'table',attrs:{columnWidths:[1,3],fontSize:10.5,bodyAlignment:'left'},content:[
    {type:'tableRow',content:[{type:'tableCell',content:[paragraph('角色')]},{type:'tableCell',content:[paragraph('职责与验证边界')]}]},
  ]},
  {type:'figure',attrs:{artifactId:'image',svgSha256:'a'.repeat(64)}},
]};
const saved=schema.nodeFromJSON(original).toJSON();
assert.deepEqual(saved.content[0].attrs.columnWidths,[1,3]);
assert.equal(saved.content[0].attrs.fontSize,10.5);
assert.equal(saved.content[0].attrs.bodyAlignment,'left');
assert.equal(saved.content[1].attrs.svgSha256,'a'.repeat(64));
assert.equal(saved.content[0].content[0].content[1].content[0].content[0].text,'职责与验证边界');
process.stdout.write('Tiptap JSON round-trip retains SVG and editable table export attributes.\n');
