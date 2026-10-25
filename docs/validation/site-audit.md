# Public Site Audit (#80, #28)

Acceptance of every public page family on 2026-10-13/14, against production
`f9a0a72` and a local stack built from this change, plus an owner-approved
Cloudflare zone audit for `minoshealth.pl` and `minoshealth.com`. Search Console
was checked on 2026-10-14 and 2026-10-19 (see Indexing); field performance moved
to #42.

## Method

`tests/browser/site-audit-browser.mjs` loads one representative route per family:
home, dictionaries, an ICD-10 code with a dated JGP view, ICD-9 tree and code, a
dated JGP catalog and group, the source catalog, RSK/BAW/NFZ source pages, the
grouper, the legal pages and an expected 404. Each route is checked with
JavaScript off at 1440, 390 and 320 px, again at 390 px with every computed font
size doubled (text-only 200% zoom), and with JavaScript on at 1440 and 390 px.
That is 102 checks. Each requires the expected status, a `<main>` and an `<h1>`,
no horizontal overflow, no text clipped past the viewport outside a deliberate
scroll container, no console error and no script from outside the site and
Plausible. Pages load one at a time with no typed queries, so the sweep is safe
against production:

```sh
node tests/browser/site-audit-browser.mjs /usr/bin/chromium https://minoshealth.pl <output>
```

Screenshots at 390 px, plain and zoomed, are written beside `site-audit.json`
and were reviewed by eye.

## Findings

- Cloudflare Email Address Obfuscation was on. Every page served through the
  zone showed the contact address as "[email protected]" without JavaScript,
  linked it to a Cloudflare page, and injected
  `/cdn-cgi/scripts/.../email-decode.min.js`; the origin HTML was correct. All
  102 production checks failed on it. It is now off (see Cloudflare). Production
  then passed 101 of 102; the remaining check was the card clipping below. After
  release `fda0788` (#191, 2026-10-14) production passes all 102
  ([evidence](https://github.com/krzysztofbury/minos-health/issues/80#issuecomment-6163132074)).
  `deploy_validation verify` now refuses a homepage containing `/cdn-cgi/`, so a
  re-enabled rewrite stops the next release.
- `/gruper` without JavaScript showed a permanent "Sprawdzamy dostępność" status
  and step buttons that could not work next to the "Formularz wymaga JavaScript"
  notice. The builder now starts hidden; the script reveals it and replaces the
  notice with the ruleset status. The grouping browser smoke passes unchanged.
- An NFZ attachment name with no break opportunity
  (`tabela_2_wykaz_rang_procedur_medycznych_...`) ran past a 390 px source card.
  Card links now wrap anywhere.
- At 320 px with 200% text the longest Polish words in JGP headings and badges
  are clipped. That combination (an effective 160 px layout) is beyond the
  accepted 390 px zoom bar and is recorded, not fixed.

## Redirects And Crawling

| Request | Result |
| --- | --- |
| Request | Before | After |
| --- | --- | --- |
| `http://minoshealth.pl/` | 301 to HTTPS | unchanged |
| `http(s)://www.minoshealth.pl/<path>?q` | HTTP took two hops; query kept | one 301 to `https://minoshealth.pl/<path>`, no query |
| `http(s)://www.minoshealth.com/<path>?q` | two hops via the `.com` apex; query kept | one 301 to `https://minoshealth.pl/<path>`, no query |
| `https://minoshealth.com/<path>` | origin 308 to `.pl`, path kept; with a query, 404 | unchanged |

Both zones' `www` rules declared `preserve_query_string: false` but built the
target with `wildcard_replace(http.request.full_uri, ...)`, and `full_uri`
already contains the query. They now match `http.host eq "www.<zone>"` and
target `concat("https://minoshealth.pl", http.request.uri.path)`. Typed text
never reached the origin either way: the edge refuses query strings with 404.
Code pages answer `HEAD` with 405 (GET-only routes); crawlers use GET.

`robots.txt` allows everything except `/api/` and names one sitemap index with
the pages (5 URLs), ICD-10 (12,371) and ICD-9 (10,180) sitemaps. Home,
dictionaries, code and source pages are `index, follow` with self canonicals.
Dated JGP pages, the grouper and the legal pages are `noindex`, as decided in
`docs/v0.0.1/decisions.md`; making JGP pages indexable is an owner decision.

## Cloudflare (#28)

Read through the Cloudflare API with owner OAuth on 2026-10-14; each change was
approved by the owner and checked live afterwards.

- Changed in both zones: Email Address Obfuscation off; the `www` redirect rules
  above; Web Analytics (RUM) auto-install disabled (it had not reached pages,
  since the CSP blocks it, and the privacy policy names only Plausible);
  Network Error Logging off, so browsers no longer send failure reports with
  URLs to `a.nel.cloudflare.com` and responses carry no `nel`/`report-to`
  headers.
- No Logpush jobs at zone or account level, no Page Rules, Rocket Loader off.
  Zone rulesets are the managed defaults, the `.pl` per-IP rate limit on
  `/api/v1/group` and a default leaked-credential rule on `.com`.
- The log-retention control answers Unauthorized: it is an Enterprise feature,
  so these Free-plan zones keep only Cloudflare's default security-event window.
- Other zones in the account were not read or changed.

Re-read on 2026-10-25, read-only, nothing changed: both zones still have no
Logpush jobs (none at account level either), no Page Rules, RUM, Network Error
Logging, Email Address Obfuscation and Rocket Loader off, the same managed and
zone rulesets, and the retention control is unavailable on Free (HTTP 405).
Zaraz is on with auto-inject but holds no tools, and the live home, sources and
code pages carry no Zaraz or Cloudflare analytics script and no `nel` or
`report-to` header; the CSP allows only same-origin scripts and Plausible.

## Indexing

The domain property `sc-domain:minoshealth.pl` is new. On 2026-10-14 the
homepage was indexed, and the sitemap index had never been submitted; the owner
approved submitting `https://minoshealth.pl/sitemap.xml`. On 2026-10-19 it read
Success with 22,556 URLs discovered: exactly 5 pages, 12,371 ICD-10 and 10,180
ICD-9 URLs, so all three child sitemaps were read. The Pages report was still
processing, which is normal for a new property. Indexing proceeds on Google's
side; no site change is pending.

## Open

- Field performance: the keyless PageSpeed Insights quota was exhausted, and the
  origin is unlikely to have CrUX data yet. Search Console Core Web Vitals or a
  CrUX key is needed (#42).
- Practitioner (#158, #126, #25) and legal evidence (#3) remain outside this
  audit.
