/* Real Tiptap/ProseMirror JSON and completion transactions, without a DOM/server. */
const fs=require('node:fs');
const path=require('node:path');
const Module=require('node:module');
const root=path.resolve(__dirname,'..');
const frontend=path.join(root,'frontend');
const esbuild=require(path.join(frontend,'node_modules/esbuild'));
const main=fs.readFileSync(path.join(frontend,'src/main.tsx'),'utf8');
const stable=main.slice(main.indexOf('const StableIds = Extension.create('),main.indexOf('const Citation = Node.create('));
const citation=main.slice(main.indexOf('const Citation = Node.create('),main.indexOf('let citationLabels:'));
if(!stable||!citation)throw new Error('Main editor schema definitions were not found');
const entry=`
import assert from 'node:assert/strict';
import {Editor,Extension,Node,mergeAttributes} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {ResearchAttributes,researchAtoms} from './src/editor/ResearchContentExtensions';
import {tiptapPort} from './src/editor/EditorPort';
type Row=Record<string,any>;
let citationLabels:Row={},citationTitles:Row={};
${stable}
${citation}
const mapping={local_id:'c1',text:'已有研究结论。',text_hash:'synthetic-text-hash',start_cp:0,end_cp:7,
               rendered_start_cp:0,rendered_end_cp:7,support:'not_checked',source_records:[]};
const input={type:'doc',content:[{type:'paragraph',attrs:{nodeId:'p1',sourceRefs:[{type:'source',id:'s1',version:'v1'}],claimEvidence:[mapping]},
content:[{type:'text',text:'已有研究结论。'},{type:'citation',attrs:{nodeId:'cite1',citationId:'ref1',claimMapping:mapping}},
         {type:'text',text:'后文。'}]}]};
for(const [name,extensions] of [
 ['research',[StarterKit,ResearchAttributes,...researchAtoms()]],
 ['main',[StarterKit,StableIds,Citation]],
] as const){
 const editor=new Editor({element:null,extensions:[...extensions],content:input});
 let saved=editor.getJSON();
 assert.deepEqual(saved.content![0].attrs!.claimEvidence,[mapping],name+' drops paragraph mapping');
 assert.deepEqual(saved.content![0].content![1].attrs!.claimMapping,mapping,name+' drops citation mapping');
 editor.commands.insertContentAt(editor.state.doc.content.size-1,{type:'text',text:'追加内容。'});
 saved=editor.getJSON();
 assert.deepEqual(saved.content![0].attrs!.claimEvidence,[mapping],name+' loses mapping on transaction');
 assert.deepEqual(saved.content![0].content!.find((n:any)=>n.type==='citation')!.attrs!.claimMapping,mapping);
 const reloaded=new Editor({element:null,extensions:[...extensions],content:saved});
 assert.deepEqual(reloaded.getJSON(),saved,name+' JSON save/reload differs');
 editor.destroy();reloaded.destroy();
}
const editor=new Editor({element:null,extensions:[StarterKit,ResearchAttributes,...researchAtoms()],
 content:{type:'doc',content:[{type:'paragraph',attrs:{nodeId:'completion-p',sourceRefs:[{type:'source',id:'prior',version:'v0'}]},
 content:[{type:'text',text:'😀前文。'}]}]}});
editor.commands.setTextSelection(editor.state.doc.content.size-1);
const port=tiptapPort(editor,()=> 'r1',async()=>true,()=>{},()=>{});
const captured=port.completionContext()!;
const next={...mapping,local_id:'c2',text:'新结论。',start_cp:0,end_cp:4,rendered_start_cp:0,rendered_end_cp:4};
const value={...captured,revision_id:'r1',text:'新结论。',can_insert:true,
 inline_content:[{type:'text',text:'新结论。'},{type:'citation',attrs:{nodeId:'new-citation',citationId:'ref2',claimMapping:next}}],
 claim_evidence:[next],source_refs:[{type:'source',id:'new',version:'v2'}]};
assert.equal(port.insertCompletion(value),true);
const inserted=editor.getJSON().content![0];
assert.equal(inserted.content!.filter((n:any)=>n.type==='citation').length,1,'completion lost native citation');
assert.deepEqual(inserted.attrs!.sourceRefs,[{type:'source',id:'prior',version:'v0'},{type:'source',id:'new',version:'v2'}]);
assert.equal(inserted.attrs!.claimEvidence[0].start_cp,4,'completion uses Unicode code points');
assert.equal(inserted.content!.find((n:any)=>n.type==='citation')!.attrs!.claimMapping.start_cp,4);
assert.equal(port.insertCompletion(value),false,'stale completion snapshot must not insert twice');
editor.destroy();
console.log('Main/research JSON roundtrip, editing, precise completion and stale-snapshot rejection passed.');
`;
const output=esbuild.buildSync({stdin:{contents:entry,loader:'ts',resolveDir:frontend},platform:'node',format:'cjs',
  bundle:true,packages:'external',write:false,target:'node22'}).outputFiles[0].text;
const filename=path.join(frontend,'writing-evidence-editor-test.cjs');
const compiled=new Module(filename,module);
compiled.filename=filename;
compiled.paths=Module._nodeModulePaths(frontend);
compiled._compile(output,filename);
