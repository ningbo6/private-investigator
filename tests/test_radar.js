const fs = require('node:fs');
const vm = require('node:vm');
const assert = require('node:assert/strict');
const source = fs.readFileSync('app.js', 'utf8');
const target = {innerHTML: ''};
const sandbox = {$: () => target, esc: s => String(s)};
vm.createContext(sandbox);
vm.runInContext(source.slice(source.indexOf('const FIVE_DIMENSIONS'), source.indexOf('function sourceKey')), sandbox);
vm.runInContext(source.slice(source.indexOf('function renderRadar('), source.indexOf('function renderCredibility(')), sandbox);
const ids = ['product', 'business', 'technology', 'risk', 'organization'];
const report = {
  dimensions: ids.map(id => ({id, name: id})),
  routes: ids.map(id => ({id, ok: true, pages: [{id: 'clue-' + id, related: true}]})),
  credibility: {status: 'complete', assessments: ids.map((id, i) => ({task_id: id, status: 'assessed', score: 50 + i * 5}))}
};
const draw = r => { sandbox.renderRadar(r); return target.innerHTML; };
let html = draw(report);
for (const a of report.credibility.assessments) {
  assert.ok(html.includes(`data-dimension="${a.task_id}" data-score="${a.score}"`));
}
const original = html;
report.credibility.assessments.reverse();
assert.equal(draw(report), original, 'score mapping must not depend on array order');
report.credibility.assessments.find(a => a.task_id === 'product').score = 0;
assert.ok(draw(report).includes('data-dimension="product" data-score="0"'), 'valid zero is preserved');
for (const status of ['citation_invalid', 'insufficient', 'failed']) {
  report.credibility.assessments.find(a => a.task_id === 'product').status = status;
  assert.ok(!draw(report).includes('data-dimension="product"'), 'invalid status must not plot');
  assert.ok(target.innerHTML.includes('暂无评分'));
}
for (const score of [null, undefined, '80', -1, 101]) {
  Object.assign(report.credibility.assessments.find(a => a.task_id === 'product'), {status: 'assessed', score});
  assert.ok(!draw(report).includes('data-dimension="product"'));
}
delete report.credibility;
assert.ok(!draw(report).includes('class="radar-dot"'), 'no fallback to clue counts');
assert.ok(!target.innerHTML.includes('class="radar-data"'), 'no missing values drawn as polygon');
console.log('Radar mapping, missing values, invalid citations, zero and historical report checks passed');

report.dimensions = report.dimensions.slice(0, 2);
assert.equal((draw(report).match(/class="radar-label"/g)||[]).length, 5, 'all five axes remain visible even with incomplete dimension metadata');
