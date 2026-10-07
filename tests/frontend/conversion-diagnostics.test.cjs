const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const source = fs.readFileSync('app/static/errors.js', 'utf8');
const context = vm.createContext({});
vm.runInContext(source.slice(source.indexOf('function escapeHtml'), source.indexOf('async function refresh')), context);
test('conversion guidance escapes server text and links to replacement upload', () => {
  const html = context.conversionGuidance({conversion_diagnostic: {
    title: 'Unsupported file', explanation: '<script>bad</script>', action: 'Convert to PDF',
  }});
  assert.ok(html.includes('Unsupported file'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(!html.includes('<script>'));
  assert.ok(html.includes('href="/add-book"'));
});
test('unrelated failures have no unsupported-file guidance', () => {
  assert.equal(context.conversionGuidance({error_message: 'Network timeout'}), '');
});
