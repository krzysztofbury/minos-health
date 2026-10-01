# Self-hosted fonts

- Atkinson Hyperlegible Next 400/600/700, Copyright 2020-2024 The Atkinson
  Hyperlegible Next Project Authors
  (<https://github.com/googlefonts/atkinson-hyperlegible-next>).
- Atkinson Hyperlegible Mono 400/600, Copyright 2020-2024 The Atkinson
  Hyperlegible Mono Project Authors
  (<https://github.com/googlefonts/atkinson-hyperlegible-mono>).

Both are licensed under the SIL Open Font License 1.1 (`OFL.txt`). The files
are subsets of the Google Fonts static instances (v7 Next, v8 Mono) to Basic
Latin, Latin-1, Latin Extended-A and common punctuation, which covers Polish:

```sh
pyftsubset <font>.ttf --flavor=woff2 --layout-features='*' \
  --unicodes="U+0020-007E,U+00A0-00FF,U+0100-017F,U+2010-2027,U+2030-203A,U+20AC,U+2122,U+2190-2193,U+2212,U+2215,U+00D7"
```

They were chosen because they distinguish I, l and 1 and O and 0, which
matters when reading ICD codes such as I10, O80 or Z00. The site is served
same-origin under `font-src 'self'`; no third-party font request is made.
