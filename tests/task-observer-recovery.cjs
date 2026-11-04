const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const ts = require('../frontend/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/tasks/taskRepository.ts'), 'utf8');
const output = ts.transpileModule(source, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.CommonJS } }).outputText;
async function scenario(replies) {
  let reads = 0;
  const sent = [];
  const context = { exports: {}, require: () => ({ api: async (_path, options) => {
    assert.equal(options.timeout, 10000); reads++;
    const value = replies.shift(); if (value instanceof Error) throw value; return value;
  }, send: (...args) => { sent.push(args); } }),
    window: { dispatchEvent() {} }, CustomEvent: class {},
    sessionStorage: { getItem() { return null; }, setItem() {} },
    setTimeout(callback) { queueMicrotask(callback); }, Promise, Set, Map };
  vm.runInNewContext(output, context);
  const observer = context.exports;
  const one = observer.observeTask('synthetic');
  const two = observer.observeTask('synthetic');
  assert.equal(one, two);
  return { result: await one, reads, sent };
}
(async () => {
  const task = { resource_type: 'task', id: 'synthetic', project_id: 'project', kind: 'write', progress: 30, status: 'running' };
  const recovered = await scenario([task, new TypeError('offline'), { ...task, status: 'succeeded', progress: 100 }]);
  assert.equal(recovered.result.status, 'succeeded'); assert.equal(recovered.reads, 3); assert.equal(recovered.sent.length, 0);
  const auth = new Error('expired session'); auth.status = 401;
  await assert.rejects(scenario([auth]), /expired session/);
  process.stdout.write('Task read recovery and single observation passed.\n');
})().catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
