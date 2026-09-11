# Issue 4: JGP Qualification Workflow Validation

Status: preliminary practitioner recollection, recorded 2026-09-09; not an
independently verified case set

Issue: <https://github.com/krzysztofbury/minos-health/issues/4>

## Evidence Scope

A practising surgeon replied on 2026-09-07 with three examples from memory.
No patient identifiers or patient-level facts were provided. The examples are
useful discovery evidence, but they do not yet establish that a code choice or
JGP result was correct for any encounter.

The reported code descriptions were checked on 2026-09-09 against:

- NFZ ICD-9 PL CM version 5.89, published 2026-09-01:
  <https://www.nfz.gov.pl/dla-swiadczeniodawcy/slowniki/pliki-icd-9-pl/>;
- RSK ICD-10 PL HCD `00CD10`, version `013`, XML description
  `ICD10_2008PLv.2026_07_20`;
- NFZ JGP algorithm version 1.1.2026, table 2 procedure ranks:
  <https://www.nfz.gov.pl/dla-swiadczeniodawcy/jednorodne-grupy-pacjentow/komunikaty-jgp/informacja-dotyczaca-publikacji-opisu-algorytmu-jgp,54.html>.

These current dictionaries can verify code labels and procedure ranks. They
cannot verify historical JGP qualification without the encounter date and the
ruleset that governed that date.

## Reported Examples

### Appendicitis

The surgeon reported that appendicitis is commonly represented by diagnosis
`K35` and laparoscopic appendectomy by procedure `47.01`. For a longer
operation or a stay lasting several days, the recollection was that diagnosis
`K65` and procedure `46.81` or `54.512` may instead lead to a different NFZ
settlement result.

Dictionary findings:

- `K35` is the category for acute appendicitis. Its child codes distinguish
  appendicitis with generalized peritonitis, with peritoneal abscess, and an
  unspecified form.
- `K65` is the category for peritonitis. It also has more specific child codes.
- `47.01` is laparoscopic appendectomy.
- `46.81` is intra-abdominal manipulation of the small intestine.
- `54.512` is open release of peritoneal intestinal adhesions.
- All three reported procedure codes have rank 3 in the 2026 NFZ procedure-rank
  table.

The labels are broadly consistent with the recollection, but procedure duration
and length of stay do not by themselves establish peritonitis, intestinal
manipulation, or adhesiolysis. Those diagnoses and procedures must be present
in the clinical and operative documentation. The exact diagnosis child code,
diagnosis role, performed procedures, JGP candidates, selected group, points,
and governing ruleset remain unknown.

### Inguinal Hernia

The surgeon reported diagnosis `K40.2` with procedure `53.09`, and sometimes
procedure `54.73` when the hernia sac is opened and removed.

Dictionary findings:

- `K40.2` is bilateral inguinal hernia without obstruction or gangrene.
- `53.09` is another unilateral inguinal hernia operation.
- `54.73` is another repair of the peritoneum.
- Both procedures have rank 3 in the 2026 NFZ procedure-rank table.

There is a laterality mismatch between the reported diagnosis and `53.09` that
needs clarification. The dictionary label for `54.73` does not establish that
opening or removing a hernia sac is sufficient to report that procedure. The
operative record, exact laterality, JGP candidates, points, and source rule are
required before this can become a case fixture.

### Perianal Fistula

The surgeon reported that a perianal fistula is usually represented by
procedure category `49.1`, but that probing the fistula may be represented by
`46.43`. No diagnosis code was supplied.

Dictionary findings:

- `49.1` is the parent category for incision or excision of an anal fistula.
  Its current leaf procedures are `49.11` and `49.12`.
- `46.43` is another revision of a colonic fistula, not probing a perianal
  fistula.
- `46.43`, `49.11`, and `49.12` have rank 3 in the 2026 NFZ procedure-rank
  table. The parent category `49.1` is not listed as a ranked procedure.

The `46.43` pairing is not supported by the current dictionary description and
may be a remembered or transcribed code error. It must be clarified rather
than corrected by inference. The diagnosis, exact documented action, final
procedure code, JGP candidates, points, and source rule are missing.

## Product Interpretation

The response supports the problem hypothesis: users compare multiple coding
and grouping paths around one hospitalization, and small differences in the
documented diagnosis or performed procedure can affect the result. It does not
yet prove which paths are valid or how often this occurs.

The existing product direction already accommodates the useful parts:

- `v0.0.1` can provide release-specific diagnosis and procedure lookup,
  directional-list membership, group conditions, procedure ranks, points, and
  source provenance;
- `v0.0.2` can evaluate one complete documented encounter, show the selected
  group, and explain candidates and near misses through a rule trace;
- `v0.0.3` may compare documented alternatives, but must never propose a
  diagnosis or procedure only because it produces more points.

The encounter workflow must keep four layers distinct:

1. documented clinical and operative facts;
2. diagnosis and procedure codes assigned from those facts;
3. JGP candidates produced by a dated ruleset;
4. the final submitted and NFZ-verified settlement result.

A candidate comparison should make changed inputs visible and require the user
to attest that every alternative is documented. Point values should be output,
not the reason Minos supplies a new clinical fact or code.

## Acceptance Status

| Issue requirement | Status after this reply |
| --- | --- |
| Two to three anonymized historical scenarios | Partial: three recollections, no complete historical encounters |
| Current workflow, tools, elapsed time, uncertainty, and documentation | Missing |
| Coding or settlement owner interview | Missing |
| User, beneficiary, buyer, and audit-risk owner | Missing |
| Candidate groups, final group, points, and exact source | Missing |
| Scope, UI, and golden-case implications | Preliminary hypotheses recorded above |

Issue 4 remains open. No source manifest, canonical terminology data, grouper
fixture, golden case, or expected result should be created from this reply.

## Follow-Up Questions

For each example, request one real anonymized historical encounter with:

1. service date or at least settlement month and year;
2. age bracket, department, admission and discharge modes, and length of stay;
3. principal and secondary diagnosis codes with their roles;
4. every performed procedure code and procedure date;
5. the relevant facts from the operative description, paraphrased without
   patient information;
6. all JGP candidates considered, the final group and product code, and points
   by settlement mode;
7. the tool used, elapsed time, uncertainty, and how the final decision was
   documented;
8. the NFZ table, list, rule, or other source used to justify each candidate;
9. whether the submitted result passed NFZ claim verification;
10. an introduction to the person responsible for coding or settlement.

Specific clarifications are also needed:

- Was `K35` or `K65` the principal diagnosis, and which full child code was
  submitted?
- Were `46.81` and `54.512` actually performed and documented, or only
  considered during settlement?
- Was the `K40.2` case bilateral while only one side was operated on?
- Is `46.43` the intended fistula code, and what exact performed action was
  documented?

## Promotion Gate

A reported example can become a golden-case seed only after its input facts,
ruleset release, expected group, points, and comparison result are recorded and
reviewed independently. A later ward-validation case may be promoted to the
golden suite only after all identifiers and patient data have been removed and
any divergence has a written disposition.
