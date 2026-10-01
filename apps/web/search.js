"use strict";
(() => {
  const query = document.getElementById("query");
  const release = document.getElementById("release");
  const state = document.getElementById("state");
  const results = document.getElementById("results");
  const provenance = document.getElementById("provenance");
  const labels = { exact_code: "Dokładny kod", exact_description: "Dokładny opis", prefix_tokens: "Fragmenty tekstu", morphology_tokens: "Odmiana wyrazów", substring: "Fragment opisu" };
  let sources = [];
  let timer;
  let controller;
  let generation = 0;
  function element(tag, text, className) {
    const node = document.createElement(tag);
    if (text !== undefined) node.textContent = text;
    if (className) node.className = className;
    return node;
  }
  function source() { return sources.find((item) => item.release_id === release.value); }
  function showProvenance() {
    provenance.replaceChildren();
    const selected = source();
    if (!selected) return;
    provenance.append(element("h2", `${selected.publisher} - ${selected.source_version}`));
    provenance.append(element("p", `Wersja źródła: ${selected.release_id}. ${selected.origin === "czs" ? "Pełny katalog CZS ze statusami i datami. Źródło nie zawiera hierarchii." : "Osobny skoroszyt NFZ z hierarchią. Nie zawiera wszystkich kodów CZS ani statusów i dat. Opisy mogą się różnić."}`));
    const url = new URL(selected.source_url);
    if (url.protocol === "https:" && ["www.nfz.gov.pl", "slowniki.nfz.gov.pl", "rsk.ezdrowie.gov.pl"].includes(url.hostname)) {
      const link = element("a", "Pobierz plik bezpośrednio z oficjalnego źródła");
      link.href = url.href; link.target = "_blank"; link.rel = "noopener noreferrer";
      provenance.append(link);
    }
  }
  function render(answer) {
    if (answer.api !== 1 || !Array.isArray(answer.results) || answer.results.length > 20) throw new Error("invalid response");
    results.replaceChildren();
    if (Array.isArray(answer.morphology) && answer.morphology.length) {
      results.append(element("p", `Analiza językowa (${answer.morphology_version}): ${answer.morphology.map((item) => `${item.token} -> ${item.alternatives.join(", ")}`).join("; ")}. Alternatywy językowe, nie synonimy medyczne.`, "evidence"));
    }
    if (Array.isArray(answer.interpretation) && answer.interpretation.length) results.append(element("p", `Interpretacja zapytania: ${answer.effective_query}.`, "evidence"));
    for (const hit of answer.results) {
      const card = element("article");
      card.tabIndex = 0;
      const heading = element("h2");
      heading.append(element("span", hit.code || "Bez kodu", "code"), element("span", hit.label));
      card.append(heading, element("span", labels[hit.match_type] || hit.match_type, "badge"));
      const attributes = hit.source_attributes || {};
      if (attributes.status) card.append(element("span", `Status źródła: ${attributes.status}`, attributes.status === "N" ? "badge inactive" : "badge"));
      for (const name of ["data_od", "data_do", "level"]) if (attributes[name]) card.append(element("span", `${name}: ${attributes[name]}`, "badge"));
      if (hit.match.description_type === "context") {
        const marker = hit.match.source_attributes.evidence_kind || "Kontekst źródła";
        card.append(element("p", `${marker}: ${hit.match.text}`, "evidence"));
      } else if (hit.match.text !== hit.label) {
        card.append(element("p", `${hit.match.description_type}: ${hit.match.text}`, "evidence"));
      }
      const evidence = element("details");
      evidence.append(element("summary", "Dokładne atrybuty i pochodzenie dowodu"));
      const attributesList = element("ul");
      for (const [name, value] of Object.entries(attributes)) attributesList.append(element("li", `${name}: ${value}`));
      for (const [name, value] of Object.entries(hit.match.source_attributes || {})) if (attributes[name] !== value) attributesList.append(element("li", `Dopasowanie - ${name}: ${value}`));
      attributesList.append(element("li", `Tekst dopasowania: ${hit.match.text}`), element("li", `Źródłowy kod: ${hit.source_code}`), element("li", `SHA-256 źródła: ${answer.provenance.source_sha256}`));
      evidence.append(attributesList); card.append(evidence);
      if (hit.context.nodes.length > 1) {
        const details = element("details");
        details.append(element("summary", "Pełny kontekst i powiązania źródła"));
        const list = element("ul");
        for (const node of hit.context.nodes) list.append(element("li", `${node.code || "Tekst"}: ${node.label}`));
        details.append(list);
        const codes = new Map(hit.context.nodes.map((node) => [node.ordinal, node.code || "Tekst"]));
        for (const edge of hit.context.edges) details.append(element("p", `${codes.get(edge.parent)} -> ${codes.get(edge.child)} (contains)`));
        card.append(details);
      }
      results.append(card);
    }
    const count = answer.results.length;
    state.textContent = count ? `${count} wyników${answer.truncated ? " (limit listy)" : ""}. Wybrana wersja: ${answer.release_id}.` : "Brak wyników w wybranej wersji źródła.";
    if (Array.isArray(answer.suggestions) && answer.suggestions.length) state.textContent += ` Propozycje pisowni (nie wykonano): ${answer.suggestions.map((item) => item.query).join("; ")}.`;
  }
  async function search() {
    const value = query.value.trim();
    const current = ++generation;
    if (controller) controller.abort();
    if (!value) { results.replaceChildren(); state.textContent = "Wpisz kod lub opis."; return; }
    const selected = source();
    if (!selected) return;
    const active = new AbortController();
    controller = active;
    const timeout = setTimeout(() => active.abort(), 5000);
    state.textContent = "Wyszukiwanie...";
    try {
      const response = await fetch("/api/v1/search", {
        method: "POST", headers: { "Content-Type": "application/json" }, cache: "no-store",
        signal: active.signal, body: JSON.stringify({ system: selected.system, release_id: selected.release_id, query: value, limit: 10 }),
      });
      if (current !== generation) return;
      if (!response.ok) { results.replaceChildren(); state.textContent = response.status === 429 ? "Limit zapytań. Spróbuj za chwilę." : "Nie można wykonać zapytania. Użyj maksymalnie 8 słów i 256 znaków."; return; }
      const answer = await response.json();
      if (current === generation) render(answer);
    } catch (error) {
      if (current === generation) { results.replaceChildren(); state.textContent = error.name === "AbortError" ? "Zapytanie przerwane lub przekroczono czas." : "Usługa chwilowo niedostępna."; }
    } finally { clearTimeout(timeout); }
  }
  query.addEventListener("input", () => { clearTimeout(timer); generation += 1; if (controller) controller.abort(); timer = setTimeout(search, 250); });
  release.addEventListener("change", () => { clearTimeout(timer); showProvenance(); search(); });
  query.addEventListener("keydown", (event) => { if (event.key === "Enter") { clearTimeout(timer); search(); } });
  query.addEventListener("keydown", (event) => { const first = results.querySelector("article"); if (event.key === "ArrowDown" && first) { event.preventDefault(); first.focus(); } });
  results.addEventListener("keydown", (event) => {
    const card = event.target.closest("article");
    if (!card) return;
    if (event.key === "ArrowDown") { event.preventDefault(); (card.nextElementSibling || card).focus(); }
    if (event.key === "ArrowUp") { event.preventDefault(); if (card.previousElementSibling) card.previousElementSibling.focus(); else query.focus(); }
    if (event.key === "Enter" && event.target === card) { const details = card.querySelector("details"); if (details) details.open = !details.open; }
  });
  document.addEventListener("keydown", (event) => { if (event.key === "/" && !["INPUT", "SELECT", "TEXTAREA"].includes(document.activeElement.tagName)) { event.preventDefault(); query.focus(); } });
  fetch("/api/v1/releases", { cache: "no-store" }).then((response) => {
    if (!response.ok) throw new Error("unavailable");
    return response.json();
  }).then((answer) => {
    if (answer.api !== 1 || !Array.isArray(answer.releases) || answer.releases.length < 1 || answer.releases.length > 16) throw new Error("invalid metadata");
    sources = answer.releases;
    release.replaceChildren(...sources.map((item) => {
      const option = element("option", `${item.system.toUpperCase()} ${item.source_version} - ${item.origin === "czs" ? "CZS: pełny katalog, statusy i daty" : "NFZ: osobna hierarchia i uwagi"}`);
      option.value = item.release_id;
      return option;
    }));
    release.disabled = false; query.disabled = false;
    state.textContent = "Wpisz kod lub opis. Dane historyczne, nie automatycznie najnowsza wersja.";
    showProvenance();
  }).catch(() => { state.textContent = "Usługa chwilowo niedostępna."; });
})();
