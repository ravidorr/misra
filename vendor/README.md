# Vendored scripts

Third-party scripts the dashboard loads from its own origin, so the page's Content-Security-Policy needs no CDN.
`build_dashboard.py` copies each one into `docs/` and puts its Subresource Integrity hash in the page, computed from
the file as built.

| File | Package | Version | SHA-256 of the file |
| --- | --- | --- | --- |
| `supabase.js` | `@supabase/supabase-js` (MIT) | 2.118.0 | `ee3be8de192b97cb1d73d1e95d6b82bb1f6b658c0f97c4ef757fc331996ed3d3` |

`supabase.js` is `dist/umd/supabase.js` of the npm package, unmodified. It defines the global `supabase`, which
`templates/auth.js` uses for the Google sign-in. `tests/test_build_dashboard.py` fails when the file and the hash above
differ, so an edit cannot go unnoticed.

## Update a script

1. Pick a stable release at least two weeks old (`npm view @supabase/supabase-js time --json`).
2. `npm pack @supabase/supabase-js@<version>`, unpack it, and copy `package/dist/umd/supabase.js` over `supabase.js`.
3. Compare the package's integrity (`npm view @supabase/supabase-js@<version> dist.integrity`) with the tarball
   (`npm pack` prints it with `--json`).
4. Put the new version and `shasum -a 256 vendor/supabase.js` in the table, run the tests and the dashboard in a
   browser, and add a `CHANGELOG.md` entry.
