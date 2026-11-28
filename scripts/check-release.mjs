// Release policy gate: one SemVer bump per PR and a matching CHANGELOG entry.
//
// Modes (run from the repository root):
//   BASE_REF=<ref> node scripts/check-release.mjs       version and changelog gate
//   node scripts/check-release.mjs --extract-release-notes <version>   writes release-notes.md
//   node scripts/check-release.mjs --tag <tag>          validates a release tag against package.json
//
// Gate rules:
//   - It only applies when release-relevant files changed: package.json, tsconfig.json, manifest.json,
//     or files under src/, lib/, bin/, app/, extension/. Extend with RELEASE_TRIGGER_PATHS
//     (comma-separated path prefixes).
//   - A package.json change that touches only devDependencies is not release-relevant (dev tooling
//     bumps, which Dependabot cannot pair with a version and a changelog entry).
//   - An all-zero BASE_REF (first push of a branch) is skipped: there is nothing to compare against.
//   - When the base ref has no package.json (the very first baseline PR), only the changelog entry
//     for the current version is required.
//
// It runs in pre-push and as a required CI gate (CI is authoritative).
import { execFileSync } from 'node:child_process';
import { readFileSync, realpathSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { isDeepStrictEqual } from 'node:util';
import semver from 'semver';

const RELEASE_TRIGGER_ROOT_FILES = new Set([
  'package.json',
  'tsconfig.json',
  'manifest.json'
]);
const RELEASE_TRIGGER_DIRS = ['src/', 'lib/', 'bin/', 'app/', 'extension/'];

function envTriggerPaths() {
  return (process.env.RELEASE_TRIGGER_PATHS ?? '')
    .split(',')
    .map((entry) => entry.trim())
    .filter(Boolean);
}

export function extractChangelogNotes(changelog, version) {
  const headerPrefix = `## [${version}]`;
  const lines = changelog.split('\n');
  let collecting = false;
  const notes = [];

  for (const line of lines) {
    if (line.startsWith('## ')) {
      if (collecting) {
        break;
      }

      if (line.startsWith(headerPrefix)) {
        collecting = true;
      }

      continue;
    }

    // Link reference definitions such as "[1.2.3]: https://..." are not release notes.
    if (collecting && !/^\[[^\]]+\]:/.test(line)) {
      notes.push(line);
    }
  }

  return notes.join('\n');
}

function assertChangelogContainsVersion(changelog, version) {
  if (!changelog.includes(`## [${version}]`)) {
    throw new Error(`CHANGELOG.md must contain a section for ${version}`);
  }

  const notes = extractChangelogNotes(changelog, version);

  if (!notes.trim()) {
    throw new Error(`CHANGELOG.md section for ${version} must not be empty`);
  }

  if (!/^\s*[-*]\s+\S/m.test(notes)) {
    throw new Error(
      `CHANGELOG.md section for ${version} must contain at least one bullet`
    );
  }
}

export function validateRelease(baseVersion, currentVersion, changelog) {
  if (baseVersion === currentVersion) {
    throw new Error('package version must change');
  }

  if (semver.compare(currentVersion, baseVersion) <= 0) {
    throw new Error('package version must increase');
  }

  assertChangelogContainsVersion(changelog, currentVersion);
}

export function requiresRelease(changedFiles, extraPaths = envTriggerPaths()) {
  return changedFiles.some(
    (file) =>
      RELEASE_TRIGGER_ROOT_FILES.has(file) ||
      RELEASE_TRIGGER_DIRS.some((dir) => file.startsWith(dir)) ||
      extraPaths.some((prefix) => file.startsWith(prefix))
  );
}

export function assertTagMatchesPackageVersion(tagName, packageVersion) {
  const tagVersion = tagName.startsWith('v') ? tagName.slice(1) : tagName;

  if (tagVersion !== packageVersion) {
    throw new Error(
      `tag ${tagVersion} does not match package.json version ${packageVersion}`
    );
  }
}

export function assertReleaseNotesPresent(releaseNotes) {
  if (!releaseNotes.trim()) {
    throw new Error('release notes must not be empty');
  }
}

export function validateTaggedRelease({
  tagName,
  packageVersion,
  changelog,
  releaseNotes
}) {
  assertTagMatchesPackageVersion(tagName, packageVersion);
  assertChangelogContainsVersion(changelog, packageVersion);
  assertReleaseNotesPresent(releaseNotes);
}

// True when package.json differs from the base only in devDependencies.
export function isDevDependencyOnlyChange(basePackageJson, currentPackageJson) {
  if (!basePackageJson || !currentPackageJson) {
    return false;
  }

  const withoutDevDependencies = (packageJson) => {
    const rest = { ...packageJson };
    delete rest.devDependencies;
    return rest;
  };

  return isDeepStrictEqual(
    withoutDevDependencies(basePackageJson),
    withoutDevDependencies(currentPackageJson)
  );
}

// Returns the parsed package.json at ref, or null when the ref has no package.json.
// A ref that does not exist is an error: fetch it instead of silently skipping the gate.
function readPackageJsonAt(ref) {
  try {
    execFileSync('git', ['rev-parse', '--verify', '--quiet', `${ref}^{commit}`], {
      stdio: 'ignore'
    });
  } catch {
    throw new Error(`base ref ${ref} does not exist; run "git fetch origin" first`);
  }

  try {
    execFileSync('git', ['cat-file', '-e', `${ref}:package.json`], {
      stdio: 'ignore'
    });
  } catch {
    return null;
  }

  const packageJson = execFileSync('git', ['show', `${ref}:package.json`], {
    encoding: 'utf8'
  });

  return JSON.parse(packageJson);
}

function readPackageVersion(ref) {
  return readPackageJsonAt(ref)?.version ?? null;
}

function readChangedFiles(baseRef) {
  return execFileSync('git', ['diff', '--name-only', `${baseRef}...HEAD`], {
    encoding: 'utf8'
  })
    .trim()
    .split('\n')
    .filter(Boolean);
}

export function runReleaseGate({
  baseRef,
  readBasePackageVersion = readPackageVersion,
  readBasePackageJson = readPackageJsonAt,
  readDiffFiles = readChangedFiles,
  readCurrentPackageJson = () =>
    JSON.parse(readFileSync('package.json', 'utf8')),
  readChangelog = () => readFileSync('CHANGELOG.md', 'utf8')
}) {
  if (!baseRef) {
    throw new Error('BASE_REF is required');
  }

  // The first push of a branch has an all-zero "before" SHA: there is nothing to compare against.
  if (/^0+$/.test(baseRef)) {
    return;
  }

  const currentPackageJson = readCurrentPackageJson();
  let changedFiles = readDiffFiles(baseRef);

  if (
    changedFiles.includes('package.json') &&
    isDevDependencyOnlyChange(readBasePackageJson(baseRef), currentPackageJson)
  ) {
    changedFiles = changedFiles.filter((file) => file !== 'package.json');
  }

  if (!requiresRelease(changedFiles)) {
    return;
  }

  const baseVersion = readBasePackageVersion(baseRef);
  const currentVersion = currentPackageJson.version;
  const changelog = readChangelog();

  if (baseVersion === null) {
    // First baseline PR: the base has no package.json, so only the changelog entry can be checked.
    assertChangelogContainsVersion(changelog, currentVersion);
    return;
  }

  validateRelease(baseVersion, currentVersion, changelog);
}

function runExtractReleaseNotes(version) {
  if (!version) {
    throw new Error('release version is required');
  }

  const notes = extractChangelogNotes(
    readFileSync('CHANGELOG.md', 'utf8'),
    version
  );

  writeFileSync('release-notes.md', `${notes.trimEnd()}\n`);
}

function runTagReleaseValidation(tagName) {
  if (!tagName) {
    throw new Error('tag name is required');
  }

  const packageJson = JSON.parse(readFileSync('package.json', 'utf8'));

  validateTaggedRelease({
    tagName,
    packageVersion: packageJson.version,
    changelog: readFileSync('CHANGELOG.md', 'utf8'),
    releaseNotes: readFileSync('release-notes.md', 'utf8')
  });
}

function main() {
  const extractFlagIndex = process.argv.indexOf('--extract-release-notes');

  if (extractFlagIndex !== -1) {
    runExtractReleaseNotes(process.argv[extractFlagIndex + 1]);
    return;
  }

  const tagFlagIndex = process.argv.indexOf('--tag');

  if (tagFlagIndex !== -1) {
    runTagReleaseValidation(process.argv[tagFlagIndex + 1]);
    return;
  }

  runReleaseGate({ baseRef: process.env.BASE_REF });
}

// Compare real paths: argv[1] keeps symlinks (for example /tmp on macOS) but import.meta.url does not.
if (
  process.argv[1] &&
  realpathSync(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  main();
}
