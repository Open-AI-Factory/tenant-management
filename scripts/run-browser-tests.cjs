'use strict';
const fs = require('node:fs');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const root = path.resolve(__dirname, '..');
fs.mkdirSync(path.join(root, 'work'), {recursive: true});
for (const suite of [
  'test_quota_ui.cjs', 'test_quota_qa_ui.cjs', 'test_web_demo_ui.cjs',
  'test_usage_ui.cjs', 'test_kueue_ui.cjs',
]) {
  process.stdout.write(`\nRunning ${suite}\n`);
  const result = spawnSync(process.execPath, [suite], {
    cwd: root, stdio: 'inherit', windowsHide: true, timeout: 120000,
  });
  if (result.error) { process.stderr.write(`${result.error.message}\n`); process.exit(1); }
  if (result.status !== 0) process.exit(result.status || 1);
}
