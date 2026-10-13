# Public Site Audit (#80, #28)

Read-only acceptance of every public page family on 2026-10-13, against
production `f9a0a72` and a local stack built from this change. It covers what can
be measured from outside; Cloudflare account settings, Search Console and field
performance need the owner (see Open).

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

- Cloudflare Email Address Obfuscation is on. Every page served through the zone
  shows the contact address as "[email protected]" without JavaScript, links it
  to a Cloudflare page, and injects
  `/cdn-cgi/scripts/.../email-decode.min.js`. The origin HTML is correct. All
  102 production checks fail on this alone, apart from the clipping below; the
  local stack built from this change passes all 102. Owner action: switch the zone setting off. `deploy_validation verify`
  now refuses a homepage containing `/cdn-cgi/`, so a release will report this
  until the setting is off.
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
| `http://minoshealth.pl/` | 301 to HTTPS (the earlier 200 is gone) |
| `https://www.minoshealth.pl/<path>` | one 301 to the apex, path kept |
| `http://www.minoshealth.pl/<path>` | two hops: HTTPS first, then the apex |
| `https://minoshealth.com/<path>` | 308 to `.pl`, path kept; with a query string, 404 |
| `https://www.minoshealth.com/<path>?q` | 301 to the `.com` apex with the query, then 404 |

Typed text never reaches the origin: the edge refuses query strings with 404.
The `www.minoshealth.com` rule still copies the query into its `Location` and
takes two hops. Owner action: point it straight at
`https://minoshealth.pl/${path}` without the query.

`robots.txt` allows everything except `/api/` and names one sitemap index with
the pages (5 URLs), ICD-10 (12,371) and ICD-9 (10,180) sitemaps. Home,
dictionaries, code and source pages are `index, follow` with self canonicals.
Dated JGP pages, the grouper and the legal pages are `noindex`, as decided in
`docs/v0.0.1/decisions.md`; making JGP pages indexable is an owner decision.

## Open

- Cloudflare: Email Address Obfuscation off and the `www.minoshealth.com` rule
  (above); for #28, the zone's logging and retention settings, Logpush jobs,
  Web Analytics/RUM and Network Error Logging (responses carry a `cf-nel`
  header). Published vendor defaults are not account evidence.
- Indexing: Search Console coverage for the sitemaps. A third-party `site:`
  search returned nothing, which proves nothing either way.
- Field performance: the keyless PageSpeed Insights quota was exhausted, and the
  origin is unlikely to have CrUX data yet. Search Console Core Web Vitals or a
  CrUX key is needed.
- Practitioner (#158) and legal evidence remain outside this audit.
