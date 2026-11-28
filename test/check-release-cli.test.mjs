import { execFileSync } from 'node:child_process';
import {
  mkdirSync,
  mkdtempSync,
  readFileSync,
  rmSync,
  symlinkSync,
  writeFileSync
} from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

const repoRoot = join(fileURLToPath(import.meta.url), '..', '..');
const scriptPath = join(repoRoot, 'scripts/check-release.mjs');

// Git exports GIT_DIR and friends to hooks (always in a worktree). They would point the temporary
// repos below at the real repository, so child processes get an environment without them.
function envWithoutGit(extra = {}) {
  return Object.fromEntries(
    Object.entries({ ...process.env, ...extra }).filter(
      ([name]) => !name.startsWith('GIT_')
    )
  );
}

function withTempDir(run) {
  const root = mkdtempSync(join(tmpdir(), 'check-release-cli-'));

  try {
    return run(root);
  } finally {
    rmSync(root, { recursive: true, force: true });
  }
}

function writeReleaseFixture(root, { version, changelog, releaseNotes }) {
  writeFileSync(
    join(root, 'package.json'),
    `${JSON.stringify({ version }, null, 2)}\n`
  );
  writeFileSync(join(root, 'CHANGELOG.md'), changelog);
  writeFileSync(join(root, 'release-notes.md'), releaseNotes);
}

function runCheckReleaseCli(root, cliArgs, options = {}) {
  try {
    execFileSync(process.execPath, [options.script ?? scriptPath, ...cliArgs], {
      cwd: root,
      env: envWithoutGit(options.env),
      encoding: 'utf8',
      stdio: ['ignore', 'pipe', 'pipe']
    });

    return { ok: true, stderr: '' };
  } catch (error) {
    const stderr =
      typeof error.stderr === 'string'
        ? error.stderr
        : (error.stderr?.toString() ?? String(error.message));

    return { ok: false, stderr };
  }
}

describe('check-release CLI --tag integration', () => {
  it('accepts a valid tagged release fixture', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      writeReleaseFixture(root, {
        version: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '- Example release.\n'
      });

      return runCheckReleaseCli(root, ['--tag', 'v0.1.2']);
    });

    expect(localThis.result.ok).toBe(true);
  });

  it('rejects --tag when the tag argument is missing', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      writeReleaseFixture(root, {
        version: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '- Example release.\n'
      });

      return runCheckReleaseCli(root, ['--tag']);
    });

    expect(localThis.result.ok).toBe(false);
    expect(localThis.result.stderr).toContain('tag name is required');
  });

  it('rejects a tag that does not match package.json', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      writeReleaseFixture(root, {
        version: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '- Example release.\n'
      });

      return runCheckReleaseCli(root, ['--tag', 'v0.1.3']);
    });

    expect(localThis.result.ok).toBe(false);
    expect(localThis.result.stderr).toContain(
      'tag 0.1.3 does not match package.json version 0.1.2'
    );
  });

  it('rejects empty release-notes.md', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      writeReleaseFixture(root, {
        version: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '   \n'
      });

      return runCheckReleaseCli(root, ['--tag', 'v0.1.2']);
    });

    expect(localThis.result.ok).toBe(false);
    expect(localThis.result.stderr).toContain(
      'release notes must not be empty'
    );
  });
});

describe('check-release CLI --extract-release-notes integration', () => {
  it('writes release-notes.md from the matching changelog section', () => {
    const localThis = { notes: undefined };

    withTempDir((root) => {
      writeReleaseFixture(root, {
        version: '0.1.3',
        changelog:
          '## [0.1.3] - 2026-10-09\n\n- Harden release gates.\n\n## [0.1.2] - 2026-10-09\n\n- Older release.\n',
        releaseNotes: ''
      });

      const localThisInner = {
        result: runCheckReleaseCli(root, ['--extract-release-notes', '0.1.3'])
      };

      expect(localThisInner.result.ok).toBe(true);
      localThis.notes = readFileSync(join(root, 'release-notes.md'), 'utf8');
    });

    expect(localThis.notes).toBe('\n- Harden release gates.\n');
  });
});

function git(root, ...gitArgs) {
  execFileSync(
    'git',
    ['-c', 'user.name=test', '-c', 'user.email=test@example.com', ...gitArgs],
    { cwd: root, env: envWithoutGit(), stdio: 'ignore' }
  );
}

describe('check-release CLI gate', () => {
  it('runs through a symlinked script path instead of silently doing nothing', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      const linkedScripts = join(root, 'linked-scripts');
      symlinkSync(join(repoRoot, 'scripts'), linkedScripts);

      return runCheckReleaseCli(root, ['--tag'], {
        script: join(linkedScripts, 'check-release.mjs')
      });
    });

    expect(localThis.result.ok).toBe(false);
    expect(localThis.result.stderr).toContain('tag name is required');
  });

  it('passes the first baseline PR when the base has no package.json', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      git(root, 'init', '-b', 'main');
      writeFileSync(join(root, 'README.md'), '# demo\n');
      git(root, 'add', '.');
      git(root, 'commit', '-m', 'initial');
      git(root, 'checkout', '-b', 'chore/project-baseline');
      mkdirSync(join(root, 'src'));
      writeFileSync(join(root, 'src/index.ts'), 'export {};\n');
      writeFileSync(
        join(root, 'package.json'),
        `${JSON.stringify({ version: '0.1.0' })}\n`
      );
      writeFileSync(
        join(root, 'CHANGELOG.md'),
        '## [0.1.0] - 2026-10-09\n\n- Initial release.\n'
      );
      git(root, 'add', '.');
      git(root, 'commit', '-m', 'baseline');

      return runCheckReleaseCli(root, [], { env: { BASE_REF: 'main' } });
    });

    expect(localThis.result.ok).toBe(true);
  });

  it('skips the gate for a devDependencies-only bump and gates a dependencies bump', () => {
    const localThis = { devOnly: undefined, runtime: undefined };

    const run = (field) =>
      withTempDir((root) => {
        const write = (value) =>
          writeFileSync(
            join(root, 'package.json'),
            `${JSON.stringify({ version: '0.1.0', [field]: { a: value } })}\n`
          );

        git(root, 'init', '-b', 'main');
        write('1.0.0');
        writeFileSync(join(root, 'CHANGELOG.md'), '# Changelog\n');
        git(root, 'add', '.');
        git(root, 'commit', '-m', 'initial');
        git(root, 'checkout', '-b', 'bump');
        write('1.0.1');
        git(root, 'commit', '-am', 'bump a');

        return runCheckReleaseCli(root, [], { env: { BASE_REF: 'main' } });
      });

    localThis.devOnly = run('devDependencies');
    localThis.runtime = run('dependencies');

    expect(localThis.devOnly.ok).toBe(true);
    expect(localThis.runtime.ok).toBe(false);
    expect(localThis.runtime.stderr).toContain('package version must change');
  });

  it('fails loudly when the base ref does not exist', () => {
    const localThis = { result: undefined };

    localThis.result = withTempDir((root) => {
      git(root, 'init', '-b', 'main');
      writeFileSync(join(root, 'package.json'), '{"version":"0.1.0"}\n');
      git(root, 'add', '.');
      git(root, 'commit', '-m', 'initial');

      return runCheckReleaseCli(root, [], {
        env: { BASE_REF: 'origin/does-not-exist' }
      });
    });

    expect(localThis.result.ok).toBe(false);
  });
});
