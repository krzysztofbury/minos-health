# Synthetic RSK ICD-10 Fixtures

These fixtures contain invented descriptions, not excerpts from the licensed
dictionary. They reproduce the structure of the checksum-verified private
capture inspected for issues #11-#13. They contain no patient data.

`minimal.xml` covers source containment, unnamed containers, descriptions in
two languages, relative dotted codes, a dagger marker, numeric modifier nodes,
annotations and repeated uncoded text. Its labels are not medical reference
content. The range separator is an XML character reference to preserve the
source's Unicode punctuation without using it in authored prose.

DTD, duplicate-code and unsupported-structure fixtures are committed below.
Depth, text, byte, element and node limits are exercised by bounded generated
inputs in the parser tests, rather than committing oversized files.
Broken references and cycles belong to store tests: this XML represents parent
relationships through nesting and has no explicit parent-reference field.

No fixture or private-source validation authorizes data redistribution.

`ranking.xml` places an inclusion-term match and a narrower code before
broader title matches in source order, so result ranking is observable: titles
that begin with the query (broader codes first), then titles that contain it,
then the inclusion term.
