/**
 * `npm test`: the Python tests, then the Node picking tests.
 *
 * pytest is run with the project's own .venv when there is one, so the tests see
 * the packages install.bat put there whether or not the venv is activated. A bare
 * `pytest` picks up whichever one is first on PATH, which on a machine with a
 * system Python fails with "No module named 'engine'".
 */
const { spawnSync } = require('child_process');
const fs = require('fs');
const path = require('path');

const root = path.join(__dirname, '..');
const venv = [path.join(root, '.venv', 'Scripts', 'python.exe'), path.join(root, '.venv', 'bin', 'python')]
    .find((candidate) => fs.existsSync(candidate));
const python = venv || 'python';

function run(exe, args) {
    const result = spawnSync(exe, args, { cwd: root, stdio: 'inherit' });
    if (result.error) {
        console.error(`[meshwright] could not start ${exe}: ${result.error.message}`);
        process.exit(1);
    }
    if (result.status !== 0) process.exit(result.status || 1);
}

run(python, ['-m', 'pytest', '--cov=engine', '--cov=app', 'tests/']);
run(process.execPath, ['--test', 'tests/test_piece_picking.cjs']);
