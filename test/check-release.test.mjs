import { describe, expect, it } from 'vitest';

import {
  assertTagMatchesPackageVersion,
  extractChangelogNotes,
  isDevDependencyOnlyChange,
  requiresRelease,
  runReleaseGate,
  validateRelease,
  validateTaggedRelease
} from '../scripts/check-release.mjs';

describe('validateRelease', () => {
  it('accepts a changed semantic version represented in the changelog', () => {
    const localThis = {
      changelog: '## [0.1.1] - 2026-10-09\n\n- Fix package metadata.\n',
      result: undefined
    };

    localThis.result = validateRelease('0.1.0', '0.1.1', localThis.changelog);

    expect(localThis.result).toBeUndefined();
  });

  it('accepts a prerelease to stable version increase', () => {
    const localThis = {
      changelog: '## [1.0.0] - 2026-10-09\n\n- General availability.\n',
      result: undefined
    };

    localThis.result = validateRelease(
      '1.0.0-rc.1',
      '1.0.0',
      localThis.changelog
    );

    expect(localThis.result).toBeUndefined();
  });

  it('rejects an unchanged version', () => {
    const localThis = {
      changelog: '## [0.1.0] - 2026-10-09\n\n- Initial release.\n'
    };

    expect(() =>
      validateRelease('0.1.0', '0.1.0', localThis.changelog)
    ).toThrow('package version must change');
  });

  it('rejects a version decrease', () => {
    const localThis = {
      changelog: '## [0.1.0] - 2026-10-09\n\n- Rollback attempt.\n'
    };

    expect(() =>
      validateRelease('0.2.0', '0.1.0', localThis.changelog)
    ).toThrow('package version must increase');
  });

  it('rejects a bumped version without a matching changelog section', () => {
    const localThis = {
      changelog: '## [0.1.0] - 2026-10-09\n\n- Initial release.\n'
    };

    expect(() =>
      validateRelease('0.1.0', '0.1.1', localThis.changelog)
    ).toThrow('CHANGELOG.md must contain a section for 0.1.1');
  });

  it('rejects a bumped version with an empty changelog section', () => {
    const localThis = {
      changelog:
        '## [0.1.1] - 2026-10-09\n\n## [0.1.0] - 2026-10-09\n\n- Initial release.\n'
    };

    expect(() =>
      validateRelease('0.1.0', '0.1.1', localThis.changelog)
    ).toThrow('CHANGELOG.md section for 0.1.1 must not be empty');
  });
});

describe('extractChangelogNotes', () => {
  it('extracts notes when the section header includes a release date', () => {
    const localThis = {
      changelog:
        '## [0.1.3] - 2026-10-09\n\n- Harden release gates.\n\n## [0.1.2] - 2026-10-09\n\n- Older release.\n',
      result: undefined
    };

    localThis.result = extractChangelogNotes(localThis.changelog, '0.1.3');

    expect(localThis.result).toBe('\n- Harden release gates.\n');
  });
});

describe('requiresRelease', () => {
  it.each([
    {
      label: 'workflow and tooling paths only',
      changedFiles: [
        '.github/workflows/ci.yml',
        'scripts/check-release.mjs',
        'test/check-release.test.mjs'
      ],
      expected: false
    },
    {
      label: 'package.json only',
      changedFiles: ['package.json'],
      expected: true
    },
    {
      label: 'tsconfig.json only',
      changedFiles: ['tsconfig.json'],
      expected: true
    },
    {
      label: 'CHANGELOG.md only',
      changedFiles: ['CHANGELOG.md'],
      expected: false
    },
    {
      label: 'documentation only',
      changedFiles: ['README.md', 'docs/superpowers/plans/example.md'],
      expected: false
    },
    {
      label: 'empty diff',
      changedFiles: [],
      expected: false
    },
    {
      label: 'src change',
      changedFiles: ['.github/workflows/ci.yml', 'src/index.ts'],
      expected: true
    }
  ])('returns $expected when $label change', ({ changedFiles, expected }) => {
    const localThis = { changedFiles, result: undefined };

    localThis.result = requiresRelease(localThis.changedFiles);

    expect(localThis.result).toBe(expected);
  });
});

describe('validateTaggedRelease', () => {
  it('accepts a tag that matches package.json and non-empty release notes', () => {
    const localThis = {
      input: {
        tagName: 'v0.1.2',
        packageVersion: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '- Example release.\n'
      },
      result: undefined
    };

    localThis.result = validateTaggedRelease(localThis.input);

    expect(localThis.result).toBeUndefined();
  });

  it('rejects a tag that does not match package.json', () => {
    const localThis = {
      input: {
        tagName: 'v0.1.3',
        packageVersion: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '- Example release.\n'
      }
    };

    expect(() => validateTaggedRelease(localThis.input)).toThrow(
      'tag 0.1.3 does not match package.json version 0.1.2'
    );
  });

  it('rejects empty release notes', () => {
    const localThis = {
      input: {
        tagName: 'v0.1.2',
        packageVersion: '0.1.2',
        changelog: '## [0.1.2] - 2026-10-09\n\n- Example release.\n',
        releaseNotes: '   \n'
      }
    };

    expect(() => validateTaggedRelease(localThis.input)).toThrow(
      'release notes must not be empty'
    );
  });

  it('rejects a tag when the changelog lacks that version section', () => {
    const localThis = {
      input: {
        tagName: 'v0.1.2',
        packageVersion: '0.1.2',
        changelog: '## [0.1.1] - 2026-10-09\n\n- Old release.\n',
        releaseNotes: '- Example release.\n'
      }
    };

    expect(() => validateTaggedRelease(localThis.input)).toThrow(
      'CHANGELOG.md must contain a section for 0.1.2'
    );
  });
});

describe('assertTagMatchesPackageVersion', () => {
  it('accepts tags with or without a v prefix', () => {
    const localThis = { result: undefined };

    localThis.result = assertTagMatchesPackageVersion('v1.0.0', '1.0.0');
    expect(localThis.result).toBeUndefined();

    localThis.result = assertTagMatchesPackageVersion('1.0.0', '1.0.0');
    expect(localThis.result).toBeUndefined();
  });
});

describe('runReleaseGate', () => {
  it('requires BASE_REF', () => {
    expect(() => runReleaseGate({ baseRef: '' })).toThrow(
      'BASE_REF is required'
    );
  });

  it('skips validation on the first push of a branch (all-zero before SHA)', () => {
    const localThis = {
      diffCalls: 0,
      result: undefined
    };

    localThis.result = runReleaseGate({
      baseRef: '0000000000000000000000000000000000000000',
      readDiffFiles: () => {
        localThis.diffCalls += 1;
        return ['package.json'];
      }
    });

    expect(localThis.result).toBeUndefined();
    expect(localThis.diffCalls).toBe(0);
  });

  it('skips validation when no release-triggering paths changed', () => {
    const localThis = {
      baseVersionCalls: 0,
      result: undefined
    };

    localThis.result = runReleaseGate({
      baseRef: 'origin/main',
      readDiffFiles: () => ['README.md'],
      readBasePackageVersion: () => {
        localThis.baseVersionCalls += 1;
        return '0.1.1';
      }
    });

    expect(localThis.result).toBeUndefined();
    expect(localThis.baseVersionCalls).toBe(0);
  });

  it('validates release metadata when package.json changes', () => {
    const localThis = {
      baseRefUsed: undefined,
      packageJsonReads: 0,
      changelogReads: 0,
      result: undefined
    };

    localThis.result = runReleaseGate({
      baseRef: 'origin/main',
      readDiffFiles: () => ['package.json'],
      readBasePackageJson: () => ({ version: '0.1.1' }),
      readBasePackageVersion: (baseRef) => {
        localThis.baseRefUsed = baseRef;
        return '0.1.1';
      },
      readCurrentPackageJson: () => {
        localThis.packageJsonReads += 1;
        return { version: '0.1.2' };
      },
      readChangelog: () => {
        localThis.changelogReads += 1;
        return '## [0.1.2] - 2026-10-09\n\n- Bump lint-staged.\n';
      }
    });

    expect(localThis.result).toBeUndefined();
    expect(localThis.baseRefUsed).toBe('origin/main');
    expect(localThis.packageJsonReads).toBe(1);
    expect(localThis.changelogReads).toBe(1);
  });

  it('propagates validateRelease errors when package.json changes', () => {
    const localThis = {
      changelog: '## [0.1.2] - 2026-10-09\n\n- Bump lint-staged.\n'
    };

    expect(() =>
      runReleaseGate({
        baseRef: 'origin/main',
        readDiffFiles: () => ['package.json'],
        readBasePackageJson: () => ({ version: '0.1.2', dependencies: {} }),
        readBasePackageVersion: () => '0.1.2',
        readCurrentPackageJson: () => ({
          version: '0.1.2',
          dependencies: { a: '1.0.0' }
        }),
        readChangelog: () => localThis.changelog
      })
    ).toThrow('package version must change');
  });
});

describe('dev dependency only package.json changes', () => {
  it('detects a change limited to devDependencies', () => {
    expect(
      isDevDependencyOnlyChange(
        { version: '1.0.0', devDependencies: { a: '1.0.0' } },
        { version: '1.0.0', devDependencies: { a: '1.0.1', b: '2.0.0' } }
      )
    ).toBe(true);
  });

  it('is false when anything else changed or a side is missing', () => {
    expect(
      isDevDependencyOnlyChange({ version: '1.0.0' }, { version: '1.0.1' })
    ).toBe(false);
    expect(
      isDevDependencyOnlyChange(
        { dependencies: { a: '1.0.0' } },
        { dependencies: { a: '1.0.1' } }
      )
    ).toBe(false);
    expect(isDevDependencyOnlyChange(null, { version: '1.0.0' })).toBe(false);
  });

  it('skips the gate for a devDependencies-only package.json change', () => {
    const localThis = { baseVersionCalls: 0, result: undefined };

    localThis.result = runReleaseGate({
      baseRef: 'origin/main',
      readDiffFiles: () => ['package.json', 'package-lock.json'],
      readBasePackageJson: () => ({
        version: '0.1.1',
        devDependencies: { a: '1.0.0' }
      }),
      readCurrentPackageJson: () => ({
        version: '0.1.1',
        devDependencies: { a: '1.0.1' }
      }),
      readBasePackageVersion: () => {
        localThis.baseVersionCalls += 1;
        return '0.1.1';
      }
    });

    expect(localThis.result).toBeUndefined();
    expect(localThis.baseVersionCalls).toBe(0);
  });

  it('still gates a devDependencies change that also touches a trigger path', () => {
    expect(() =>
      runReleaseGate({
        baseRef: 'origin/main',
        readDiffFiles: () => ['package.json', 'lib/a.js'],
        readBasePackageJson: () => ({ version: '0.1.1', devDependencies: {} }),
        readCurrentPackageJson: () => ({
          version: '0.1.1',
          devDependencies: { a: '1.0.0' }
        }),
        readBasePackageVersion: () => '0.1.1',
        readChangelog: () => '## [0.1.1] - 2026-10-09\n\n- Old.\n'
      })
    ).toThrow('package version must change');
  });

  it('still gates a dependencies change', () => {
    expect(() =>
      runReleaseGate({
        baseRef: 'origin/main',
        readDiffFiles: () => ['package.json'],
        readBasePackageJson: () => ({ version: '0.1.1', dependencies: {} }),
        readCurrentPackageJson: () => ({
          version: '0.1.1',
          dependencies: { a: '1.0.0' }
        }),
        readBasePackageVersion: () => '0.1.1',
        readChangelog: () => '## [0.1.1] - 2026-10-09\n\n- Old.\n'
      })
    ).toThrow('package version must change');
  });
});

describe('changelog notes', () => {
  it('requires at least one bullet in the version section', () => {
    expect(() =>
      validateRelease('0.1.0', '0.1.1', '## [0.1.1] - 2026-10-09\n\n### Added\n')
    ).toThrow('at least one bullet');
  });

  it('does not swallow the next heading or link references into the notes', () => {
    const localThis = {
      changelog:
        '## [0.1.1] - 2026-10-09\n\n- Fixed it.\n\n## Unreleased\n\n- Pending.\n\n[0.1.1]: https://example.com\n'
    };

    expect(extractChangelogNotes(localThis.changelog, '0.1.1').trim()).toBe(
      '- Fixed it.'
    );
  });

  it('ignores link references when checking a last section for content', () => {
    expect(() =>
      validateRelease(
        '0.1.0',
        '0.1.1',
        '## [0.1.1] - 2026-10-09\n\n[0.1.1]: https://example.com\n'
      )
    ).toThrow('must not be empty');
  });
});

describe('requiresRelease trigger paths', () => {
  it('triggers on lib/, bin/, app/, extension/ and manifest.json', () => {
    expect(requiresRelease(['lib/a.js'], [])).toBe(true);
    expect(requiresRelease(['bin/cli.js'], [])).toBe(true);
    expect(requiresRelease(['app/page.tsx'], [])).toBe(true);
    expect(requiresRelease(['extension/popup.html'], [])).toBe(true);
    expect(requiresRelease(['manifest.json'], [])).toBe(true);
  });

  it('honors extra trigger path prefixes', () => {
    expect(requiresRelease(['services/api.ts'], [])).toBe(false);
    expect(requiresRelease(['services/api.ts'], ['services/'])).toBe(true);
  });
});

describe('runReleaseGate without a base package.json', () => {
  it('only checks the changelog for the current version', () => {
    const localThis = { result: undefined };

    localThis.result = runReleaseGate({
      baseRef: 'origin/main',
      readDiffFiles: () => ['package.json'],
      readBasePackageJson: () => null,
      readBasePackageVersion: () => null,
      readCurrentPackageJson: () => ({ version: '0.1.0' }),
      readChangelog: () => '## [0.1.0] - 2026-10-09\n\n- Initial release.\n'
    });

    expect(localThis.result).toBeUndefined();
  });

  it('still requires the changelog entry', () => {
    expect(() =>
      runReleaseGate({
        baseRef: 'origin/main',
        readDiffFiles: () => ['package.json'],
        readBasePackageJson: () => null,
        readBasePackageVersion: () => null,
        readCurrentPackageJson: () => ({ version: '0.1.0' }),
        readChangelog: () => '# Changelog\n'
      })
    ).toThrow('must contain a section for 0.1.0');
  });
});
