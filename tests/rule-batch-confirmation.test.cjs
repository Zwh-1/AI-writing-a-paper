const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs'), vm = require('node:vm'), path = require('node:path');
const ts = require('../frontend/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/ruleBatchConfirmation.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sandbox = { exports: {}, Error };
vm.runInNewContext(compiled, sandbox);
const plain = value => JSON.parse(JSON.stringify(value));

test('selected confirmations are unique, sequential, and restricted to the selected ids', async () => {
  const calls = [];
  let active = 0, peak = 0;
  const result = await sandbox.exports.confirmSelectedRules(['a', 'b', 'a', 'c'], async id => {
    active++; peak = Math.max(peak, active); calls.push(id);
    await Promise.resolve(); active--;
  });
  assert.deepEqual(calls, ['a', 'b', 'c']);
  assert.equal(peak, 1);
  assert.equal(result.updated, 3);
  assert.deepEqual(plain(result.failed_ids), []);
});

test('partial failures preserve exact failed selections and retry only those items', async () => {
  const calls = [];
  const first = await sandbox.exports.confirmSelectedRules(['a', 'b', 'c'], async id => {
    calls.push(id); if (id === 'b') throw new Error('保存失败');
  });
  assert.deepEqual(plain(first.confirmed_ids), ['a', 'c']);
  assert.deepEqual(plain(first.failed_ids), ['b']);
  assert.equal(first.updated, 2);
  assert.equal(first.failures[0].message, '保存失败');
  const second = await sandbox.exports.confirmSelectedRules(first.failed_ids, async id => calls.push(id));
  assert.deepEqual(calls, ['a', 'b', 'c', 'b']);
  assert.equal(second.updated, 1);
});

test('a fully rejected operation never reports successful confirmations', async () => {
  const result = await sandbox.exports.confirmSelectedRules(['a', 'b'], async () => { throw new Error('会话校验失败'); });
  assert.equal(result.updated, 0);
  assert.deepEqual(plain(result.failed_ids), ['a', 'b']);
  assert.deepEqual(plain(result.confirmed_ids), []);
  assert.equal(result.failures.length, 2);
});
