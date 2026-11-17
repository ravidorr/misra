// The assistant endpoint lists the filter values of the dashboard (supabase/functions/mcp/tools.ts). They live in
// classify.py and regions.py; this fails when they drift apart, so a role type added there cannot be forgotten here.
import { readFileSync } from 'node:fs';

import { describe, expect, it } from 'vitest';

import { LEVELS, REGIONS, ROLE_TYPES } from '../supabase/functions/mcp/tools.ts';

const classify = readFileSync(new URL('../classify.py', import.meta.url), 'utf8');
const regions = readFileSync(new URL('../regions.py', import.meta.url), 'utf8');
const constant = (source, name) => new RegExp(`^${name} = "([^"]+)"`, 'mu').exec(source)?.[1];

describe('the filter values the assistant is told', () => {
  it('are the levels of classify.py', () => {
    expect([...LEVELS]).toEqual([constant(classify, 'INTERN_LEVEL'), constant(classify, 'JUNIOR_LEVEL'), constant(classify, 'NOT_SPECIFIED')]);
  });

  it('are the role types of classify.py, then Other', () => {
    const table = /^ROLE_TYPES: dict\[str, tuple\[str, \.\.\.\]\] = \{(.*?)^\}/msu.exec(classify)?.[1] ?? '';
    const types = [...table.matchAll(/^ {4}"([^"]+)": \(/gmu)].map((match) => match[1]);
    expect(types.length).toBeGreaterThan(5);
    expect([...ROLE_TYPES]).toEqual([...types, constant(classify, 'OTHER')]);
  });

  it('are the regions of regions.py without Remote, which no job row has (the dashboard adds it for roles that do not say)', () => {
    const names = [...(/^REGION_NAMES = \(([^)]*)\)/mu.exec(regions)?.[1] ?? '').matchAll(/\w+/gu)].map((match) => match[0]);
    const values = names.map((name) => constant(regions, name));
    expect(values.at(-1)).toBe('Remote');
    expect([...REGIONS]).toEqual(values.slice(0, -1));
  });
});
