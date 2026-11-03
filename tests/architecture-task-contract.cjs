// Executes the actual TypeScript task classifier without a browser or transpilation copy.
const fs=require('node:fs'),path=require('node:path'),assert=require('node:assert/strict');
const root=path.resolve(__dirname,'..'),ts=require(path.join(root,'frontend/node_modules/typescript'));
const file=path.join(root,'frontend/src/tasks/taskRepository.ts'),source=ts.transpileModule(fs.readFileSync(file,'utf8'),{compilerOptions:{module:ts.ModuleKind.CommonJS,target:ts.ScriptTarget.ES2022}}).outputText;
const output={exports:{}};new Function('require','module','exports',source)(name=>name.includes('client')?{api:async()=>{},send:async()=>{}}:require(name),output,output.exports);
assert.equal(output.exports.isTask({id:'r',kind:'canvas',revision:1,data:{}}),false);
assert.equal(output.exports.isTask({id:'t',kind:'studio',status:'queued',progress:0}),true);
assert.equal(output.exports.isTask({resource_type:'task',id:'t',status:'future_status'}),true);
assert.deepEqual(output.exports.taskResult({result:'{"saved":true}'}),{saved:true});
console.log('Task classifier: domain records, legacy tasks, explicit task types and results verified.');
