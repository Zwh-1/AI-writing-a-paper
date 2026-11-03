/** DOM-free validation of the actual editor schema and completion adapter. */
import fs from "node:fs";
import path from "node:path";
import { createRequire } from "node:module";
import { fileURLToPath, pathToFileURL } from "node:url";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const require = createRequire(path.join(root, "frontend/package.json"));
const { build } = require("esbuild");
const output = path.join(root, ".runtime/writing-flow-ui-acceptance/editor-mapping-check.cjs");
const code = `
import assert from 'node:assert/strict';
import fs from 'node:fs';
import {getSchema} from '@tiptap/core';
import StarterKit from '@tiptap/starter-kit';
import {EditorState,TextSelection} from '@tiptap/pm/state';
import {Fragment,Slice} from '@tiptap/pm/model';
import {ResearchAttributes,researchAtoms} from './src/editor/ResearchContentExtensions';
import {tiptapPort} from './src/editor/EditorPort';
const schema=getSchema([StarterKit,ResearchAttributes,...researchAtoms()]);
const fixture=JSON.parse(fs.readFileSync(${JSON.stringify(path.join(root, ".runtime/writing-flow-ui-acceptance/task-result.json"))},'utf8'));
let citations=0,mappings=0;
for(const chapter of fixture.result.paper_sections){
 const parsed=schema.nodeFromJSON(chapter.document);const json=parsed.toJSON();
 parsed.descendants(node=>{if(node.type.name==='citation'){citations++;assert(node.attrs.citationId);assert(node.attrs.claimMapping);}if(node.attrs.claimEvidence?.length){mappings+=node.attrs.claimEvidence.length;assert(node.attrs.claimEvidence[0].source_records.length);}});
 assert.deepEqual(schema.nodeFromJSON(json).toJSON(),json);
}
assert(citations>=2&&mappings>=2);
let state=EditorState.create({schema,doc:schema.nodeFromJSON({type:'doc',content:[{type:'paragraph',attrs:{nodeId:'unicode-paragraph'},content:[{type:'text',text:'😀前文。'}]}]})});
const editor={get state(){return state;},getJSON:()=>state.doc.toJSON(),chain(){let tr=state.tr;return {focus(){return this;},setTextSelection(value){tr=tr.setSelection(TextSelection.create(tr.doc,value.from,value.to));return this;},insertContent(value){const nodes=value.map(item=>schema.nodeFromJSON(item));tr=tr.replaceSelection(new Slice(Fragment.fromArray(nodes),0,0));return this;},updateAttributes(name,attrs){const pos=tr.selection.$from.before();const parent=tr.doc.nodeAt(pos);tr=tr.setNodeMarkup(pos,undefined,{...parent.attrs,...attrs});return this;},run(){state=state.apply(tr);return true;}};}};
const port=tiptapPort(editor,()=> 'revision-one',async()=>true,()=>{},()=>{});
const position={from:state.doc.content.size-1,to:state.doc.content.size-1};
const ref={type:'source',id:'synthetic-source',version:'synthetic-version'};
const mapping={local_id:'c1',text:'新结论。',start_cp:0,end_cp:4,rendered_start_cp:0,rendered_end_cp:4,source_records:[ref]};
const suggestion={text:'新结论。',revision_id:'revision-one',snapshot:JSON.stringify(editor.getJSON()),position,source_refs:[ref],claim_evidence:[mapping],inline_content:[{type:'text',text:'新结论。'},{type:'citation',attrs:{citationId:'synthetic-reference',claimMapping:{local_id:'c1',start_cp:0,end_cp:4}}}]};
assert(port.insertCompletion(suggestion));
const paragraph=state.doc.firstChild;assert.equal(paragraph.attrs.claimEvidence[0].start_cp,4);assert.equal(paragraph.attrs.claimEvidence[0].end_cp,8);
assert.equal(paragraph.lastChild.attrs.claimMapping.start_cp,4);assert.equal(paragraph.lastChild.attrs.claimMapping.end_cp,8);
assert.deepEqual(paragraph.attrs.sourceRefs,[ref]);
const frozen=JSON.stringify(editor.getJSON());
assert.equal(port.insertCompletion({...suggestion,can_insert:false}),false);assert.equal(JSON.stringify(editor.getJSON()),frozen);
assert.equal(port.insertCompletion({...suggestion,revision_id:'stale'}),false);
const report={checks:['actual_tiptap_schema_preserves_generated_citations_and_claim_mappings','unicode_completion_offsets_preserved_through_real_prosemirror_transactions','source_refs_retained_after_completion','blocked_or_stale_completion_does_not_mutate_document'],passed:4,citations,mappings,scope:'Actual editor schema and adapter without a browser or DOM; not a visual/click test.'};
fs.writeFileSync(${JSON.stringify(path.join(root, ".runtime/writing-flow-ui-acceptance/editor-report.json"))},JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
`;
await build({ stdin: { contents: code, resolveDir: path.join(root, "frontend"), loader: "ts" },
  bundle: true, platform: "node", format: "cjs", outfile: output, logLevel: "error" });
await import(pathToFileURL(output).href);
