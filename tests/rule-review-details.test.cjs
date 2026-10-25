const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const path = require('node:path');
const ts = require('../frontend/node_modules/typescript');
const source = fs.readFileSync(path.join(__dirname, '../frontend/src/ruleReviewDetails.ts'), 'utf8');
const compiled = ts.transpileModule(source, { compilerOptions: { module: ts.ModuleKind.CommonJS, target: ts.ScriptTarget.ES2020 } }).outputText;
const sandbox = { exports: {} };
vm.runInNewContext(compiled, sandbox);
const details = (rules, events) => JSON.parse(JSON.stringify(sandbox.exports.ruleReviewDetails('p', rules, events)));
const rules = ['a', 'b', 'c'].map(id => ({ id, project_id: 'p', statement: id }));

test('task counts and failure reasons come from its saved events', () => {
  const result = details(rules, [
    { rule_ids: ['a', 'b', 'c'] },
    { rule_id: 'a', state: 'machine_verified' },
    { rule_id: 'b', state: 'invalid', error: '缺少可定位的原文依据' },
    { rule_id: 'c', state: 'needs_resolution' },
  ]);
  assert.deepEqual(result.counts, { accepted: 1, reference: 0, uncertain: 1, unreviewed: 1 });
  assert.equal(result.items[1].reason, '缺少可定位的原文依据');
});

test('current rule state cannot replace the historical task result', () => {
  const result = details([{ ...rules[0], state: 'machine_verified' }], [
    { rule_ids: ['a'] }, { rule_id: 'a', state: 'invalid', error: '旧任务未审核完成' },
  ]);
  assert.equal(result.items[0].bucket, 'unreviewed');
  assert.equal(result.items[0].reason, '旧任务未审核完成');
});

test('legacy tasks never substitute project-wide candidates for missing scope', () => {
  const result = details(rules, [{ phase: '完成', progress: 100 }]);
  assert.equal(result.scope_saved, false);
  assert.deepEqual(result.items, []);
  assert.match(result.note, /未保存逐条审核范围/);
});

test('repeated scopes and recovery attempts retain the latest decision once', () => {
  const result = details(rules, [
    { rule_ids: ['a', 'b'] }, { rule_ids: ['a', 'b'] },
    { rule_id: 'a', state: 'invalid' }, { rule_id: 'a', state: 'knowledge' },
    { rule_id: 'c', state: 'machine_verified' },
  ]);
  assert.equal(result.items.length, 2);
  assert.deepEqual(result.counts, { accepted: 0, reference: 1, uncertain: 0, unreviewed: 1 });
});

test('deleted and foreign project rules do not expose unrelated content', () => {
  const result = details([...rules, { id: 'foreign', project_id: 'q', statement: 'private' }], [{ rule_ids: ['deleted', 'foreign'] }]);
  assert(result.items.every(item => item.rule === undefined));
  assert(!JSON.stringify(result).includes('private'));
});

test('all reference types and unknown requests have distinct categories', () => {
  const result = details(rules, [
    { rule_ids: ['a', 'b', 'c'] }, { rule_id: 'a', state: 'example' },
    { rule_id: 'b', state: 'conditional_reference' }, { rule_id: 'c', state: 'unknown' },
  ]);
  assert.equal(result.counts.reference, 2);
  assert.equal(result.counts.unreviewed, 1);
  assert.match(result.items[2].reason, /远端结果尚未核实/);
});
