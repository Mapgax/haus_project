const mapEl = document.querySelector("#geoMap");
const results = document.querySelector("#results");
const priceToggle = document.querySelector("#priceToggle");
const priceSwitch = document.querySelector("#priceSwitch");
const resultCount = document.querySelector("#resultCount");
const closestLabel = document.querySelector("#closestLabel");
const medianLabel = document.querySelector("#medianLabel");
const updatedAt = document.querySelector("#updatedAt");
const filterNote = document.querySelector("#filterNote");
const resetSelection = document.querySelector("#resetSelection");
const priceToggleLabel = document.querySelector("#priceToggleLabel");
const modeLinks = document.querySelectorAll("[data-mode-link]");
const schoolToggle = document.querySelector("#schoolToggle");
const nlStreetToggle = document.querySelector("#nlStreetToggle");
const eyebrow = document.querySelector("#eyebrow");
const mapHeading = document.querySelector("#mapHeading");

const VALUATION_LABELS = {
  guenstig: "eher günstig",
  mittel: "im Mittel",
  teuer: "eher teuer",
};

const MODE_PATHS = { buy: "/", rent: "/mieten", nl: "/niederlande" };

let payload = null;
let selectedId = null;
let leafletMap = null;
let markerLayer = null;
let schoolLayer = null;
let schoolsLoaded = false;
let nlStreetLayer = null;
let nlStreetsLoaded = false;
// Ids that were unseen when the current tab was opened — they carry the
// NEU chip until the next tab switch.
let unseenAtOpen = new Set();

function modeFromPath() {
  const path = window.location.pathname;
  if (path.includes("niederlande")) return "nl";
  if (path.includes("mieten")) return "rent";
  return "buy";
}

let mode = modeFromPath();

function formatDate(value) {
  const date = new Date(value);
  return new Intl.DateTimeFormat("de-CH", {
    dateStyle: "medium",
    timeStyle: "short",
  }).format(date);
}

function formatNumber(value, digits = 1) {
  return new Intl.NumberFormat("de-CH", {
    maximumFractionDigits: digits,
    minimumFractionDigits: digits,
  }).format(value);
}

function referenceFor(tab) {
  // Old payloads (pre-NL) only carry a single `reference`.
  if (payload.references) {
    return payload.references[tab === "nl" ? "nl" : "ch"];
  }
  return payload.reference;
}

function listingsForTab(tab) {
  return payload.listings.filter((item) =>
    tab === "nl"
      ? item.region === "nl"
      : item.region !== "nl" && item.category === tab
  );
}

function visibleListings() {
  let items = listingsForTab(mode);
  if (mode === "nl") {
    // No price limit by design: sorted by the surroundings/condition score.
    return items.sort(
      (a, b) =>
        (b.nlScore ?? 0) - (a.nlScore ?? 0) ||
        (a.priceChf ?? Infinity) - (b.priceChf ?? Infinity)
    );
  }
  const strict = priceToggle.checked;
  const criteria = payload.criteria[mode];
  const limit = criteria.defaultMaxPriceChf;
  items = items.filter((item) => {
    if (!strict) return true;
    return item.priceChf !== null && item.priceChf <= limit;
  });
  return items.sort((a, b) => a.distanceKm - b.distanceKm || (a.priceChf ?? Infinity) - (b.priceChf ?? Infinity));
}

// --- unread badges -----------------------------------------------------------
// localStorage keeps the listing ids each tab has shown. A tab with ids not
// in its stored set gets a count badge; opening the tab clears it. Note that
// a price change alters the fingerprint/id, so the listing re-flags as NEU —
// same intentional semantics as the ntfy price alert.

const SEEN_PREFIX = "hausfinder.seenIds.";

function readSeen(tab) {
  try {
    const raw = localStorage.getItem(SEEN_PREFIX + tab);
    return raw ? new Set(JSON.parse(raw)) : null;
  } catch (error) {
    return null;
  }
}

function writeSeen(tab, ids) {
  try {
    // Storing exactly the current payload ids also prunes vanished listings.
    localStorage.setItem(SEEN_PREFIX + tab, JSON.stringify([...ids]));
  } catch (error) {
    /* storage full/blocked: badges silently degrade */
  }
}

function updateBadges() {
  if (!payload) return;
  ["buy", "rent", "nl"].forEach((tab) => {
    const el = document.querySelector(`[data-badge="${tab}"]`);
    if (!el) return;
    const ids = listingsForTab(tab).map((item) => item.id);
    const stored = readSeen(tab);
    if (stored === null) {
      // First visit ever: initialize silently instead of flagging the whole
      // inventory as new.
      writeSeen(tab, ids);
      el.hidden = true;
      return;
    }
    const unseen = ids.filter((id) => !stored.has(id));
    el.textContent = String(unseen.length);
    el.hidden = unseen.length === 0;
  });
}

function markTabSeen(tab) {
  if (!payload) return;
  const ids = listingsForTab(tab).map((item) => item.id);
  const stored = readSeen(tab);
  unseenAtOpen = stored
    ? new Set(ids.filter((id) => !stored.has(id)))
    : new Set();
  writeSeen(tab, ids);
  updateBadges();
}

// --- map ---------------------------------------------------------------------

function createMarkerIcon(item, index) {
  let className;
  if (mode === "nl") {
    className = item.preferredStreet
      ? "property-marker nl-marker-preferred"
      : "property-marker nl-marker";
  } else {
    const limit = payload.criteria[mode].defaultMaxPriceChf;
    className = item.priceChf !== null && item.priceChf <= limit
      ? "property-marker affordable-marker"
      : "property-marker inquiry-marker";
  }
  const selectedClass = selectedId === item.id ? " selected-marker" : "";
  return L.divIcon({
    className: `${className}${selectedClass}`,
    html: `<span>${index + 1}</span>`,
    iconSize: [30, 30],
    iconAnchor: [15, 15],
  });
}

function ensureMap() {
  if (leafletMap || !window.L) {
    return;
  }
  leafletMap = L.map(mapEl, {
    scrollWheelZoom: false,
    zoomControl: true,
  });
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
  }).addTo(leafletMap);
  markerLayer = L.layerGroup().addTo(leafletMap);
  schoolLayer = L.layerGroup();
  nlStreetLayer = L.layerGroup();
}

async function loadSchools() {
  if (schoolsLoaded || !schoolLayer) {
    return;
  }
  schoolsLoaded = true;
  try {
    const response = await fetch("assets/schools.geojson", { cache: "no-store" });
    const data = await response.json();
    const zones = data.features.filter((f) => f.properties.kind === "zone");
    const unclear = data.features.filter((f) => f.properties.kind === "unclear");
    const schools = data.features.filter((f) => f.properties.kind === "school");

    // Kerne: klar einer Schule zugeordnet -> satte Fuellung, durchgezogener Rand
    L.geoJSON(zones, {
      style: (feature) => ({
        color: feature.properties.color,
        weight: 1.3,
        fillColor: feature.properties.color,
        fillOpacity: 0.24,
        lineJoin: "round",
      }),
      onEachFeature: (feature, layer) => {
        layer.bindTooltip(feature.properties.name, { sticky: true });
        layer.bindPopup(`<strong>${feature.properties.name}</strong><br>${feature.properties.area}`);
      },
    }).addTo(schoolLayer);

    // Grenzgebiet: von mehreren Schulen erreichbar -> neutrales Grau, gestrichelt
    L.geoJSON(unclear, {
      style: () => ({
        color: "#4b535d",
        weight: 1,
        fillColor: "#8d96a1",
        fillOpacity: 0.32,
        dashArray: "3,4",
      }),
      onEachFeature: (feature, layer) => {
        layer.bindTooltip("Grenzgebiet – Zuteilung unklar", { sticky: true });
        layer.bindPopup(`<strong>${feature.properties.name}</strong><br>${feature.properties.area}`);
      },
    }).addTo(schoolLayer);

    schools.forEach((feature) => {
      const [lon, lat] = feature.geometry.coordinates;
      const props = feature.properties;
      const marker = L.marker([lat, lon], {
        icon: L.divIcon({
          className: "school-marker",
          html: `<span style="background:${props.color}">S</span>`,
          iconSize: [26, 26],
          iconAnchor: [13, 13],
        }),
        title: props.name,
      });
      marker.bindPopup(`
        <strong>${props.name}</strong><br>
        ${props.address}<br>
        <em>${props.area}</em>
      `);
      marker.addTo(schoolLayer);
    });
  } catch (error) {
    console.error("Schuldaten konnten nicht geladen werden", error);
  }
}

async function loadNlStreets() {
  if (nlStreetsLoaded || !nlStreetLayer) {
    return;
  }
  nlStreetsLoaded = true;
  try {
    // Real OSM street geometry for the whitelist streets (generated by
    // scripts/generate_nl_streets.py); may not exist yet — fail soft.
    const response = await fetch("assets/nl_streets.geojson", { cache: "no-store" });
    if (!response.ok) return;
    const data = await response.json();
    L.geoJSON(data, {
      style: () => ({ color: "#315c86", weight: 5, opacity: 0.55 }),
      onEachFeature: (feature, layer) => {
        if (feature.properties?.name) {
          layer.bindTooltip(feature.properties.name, { sticky: true });
        }
      },
    }).addTo(nlStreetLayer);
  } catch (error) {
    console.warn("NL-Strassendaten nicht verfügbar", error);
  }
}

function applyOverlayLayers() {
  if (!leafletMap) return;
  const wantSchools = mode !== "nl" && schoolToggle && schoolToggle.checked;
  const wantStreets = mode === "nl" && nlStreetToggle && nlStreetToggle.checked;
  if (wantSchools) {
    leafletMap.addLayer(schoolLayer);
  } else if (schoolLayer) {
    leafletMap.removeLayer(schoolLayer);
  }
  if (wantStreets) {
    leafletMap.addLayer(nlStreetLayer);
  } else if (nlStreetLayer) {
    leafletMap.removeLayer(nlStreetLayer);
  }
}

function drawMap(items) {
  ensureMap();
  if (!leafletMap || !markerLayer) {
    mapEl.textContent = "Karte konnte nicht geladen werden.";
    return;
  }

  const ref = referenceFor(mode);
  markerLayer.clearLayers();
  const markerBounds = [];

  if (ref) {
    const referenceMarker = L.circleMarker([ref.lat, ref.lon], {
      radius: 8,
      color: "#17202a",
      fillColor: "#17202a",
      fillOpacity: 1,
      weight: 2,
    }).bindPopup(`<strong>${ref.label}</strong>`);
    referenceMarker.addTo(markerLayer);
    markerBounds.push([ref.lat, ref.lon]);
  }

  items.forEach((item, index) => {
    const marker = L.marker([item.lat, item.lon], {
      icon: createMarkerIcon(item, index),
      title: item.title,
    });
    const roomsText = item.rooms !== null && item.rooms !== undefined ? `${item.rooms} Zi` : "Zi k.A.";
    const areaText = item.areaSqm ? `${Math.round(item.areaSqm)} m²` : "m² k.A.";
    marker.bindPopup(`
      <strong>${index + 1}. ${item.title}</strong><br>
      ${item.priceLabel} · ${formatNumber(item.distanceKm, 1)} km<br>
      ${roomsText} · ${areaText}<br>
      <a href="${item.url}" target="_blank" rel="noreferrer">Inserat öffnen</a>
    `);
    marker.on("click", () => {
      selectedId = item.id;
      render();
      document.querySelector(`[data-card-id="${selectedId}"]`)?.scrollIntoView({ block: "center", behavior: "smooth" });
    });
    marker.addTo(markerLayer);
    markerBounds.push([item.lat, item.lon]);
  });

  applyOverlayLayers();
  if (markerBounds.length) {
    leafletMap.fitBounds(markerBounds, { padding: [36, 36], maxZoom: 14 });
  }
}

// --- cards -------------------------------------------------------------------

function listingChips(item) {
  const chips = [];
  if (unseenAtOpen.has(item.id)) {
    chips.push(`<span class="pill new-chip">NEU</span>`);
  }
  if (item.waterfront) {
    chips.push(`<span class="pill waterfront-pill">Wasserlage: ${item.waterfront}</span>`);
  }
  if (item.bouwjaar) {
    const warn = item.bouwjaar >= 1960 && item.bouwjaar <= 1985 ? " bouwjaar-warn" : "";
    chips.push(`<span class="pill bouwjaar-pill${warn}">Bouwjaar ${item.bouwjaar}</span>`);
  }
  (item.conditionPlus || []).forEach((kw) => {
    chips.push(`<span class="pill condition-plus">${kw}</span>`);
  });
  (item.conditionMinus || []).forEach((kw) => {
    chips.push(`<span class="pill condition-minus">${kw}</span>`);
  });
  return chips.join("");
}

function renderCards(items) {
  if (items.length === 0) {
    const hint = mode === "nl"
      ? `<p class="filter-note">Noch keine Treffer: harte Filter (Wasserlage, keine Recreatiewoningen, 4+ Zimmer) oder der nächste Scrape-Lauf steht noch aus.</p>`
      : `<p class="filter-note">Schalte den Preisfilter aus, um auch Objekte auf Anfrage oder über dem Preislimit zu sehen.</p>`;
    results.innerHTML = `<div class="card"><h3>Keine Objekte im aktuellen Filter</h3>${hint}</div>`;
    return;
  }

  const currencySymbol = mode === "nl" ? "€" : "CHF";
  results.innerHTML = items.map((item, index) => `
    <article class="card ${selectedId === item.id ? "selected" : ""}" data-card-id="${item.id}">
      <div>
        <h3>${index + 1}. ${item.title}</h3>
        <div class="meta">
          <span class="pill">${item.propertyType}</span>
          <span class="pill">${formatNumber(item.distanceKm, 1)} km</span>
          <span class="pill">${item.rooms !== null && item.rooms !== undefined ? `${formatNumber(item.rooms, 1)} Zi` : "Zi k.A."}</span>
          <span class="pill">${item.areaSqm ? `${Math.round(item.areaSqm)} m²` : "m² k.A."}</span>
          <span class="pill">${item.locationLabel}</span>
          ${listingChips(item)}
        </div>
      </div>
      <div class="price">
        <strong>${item.priceLabel}</strong>
        <span>${item.pricePerSqm ? `${currencySymbol} ${new Intl.NumberFormat("de-CH").format(item.pricePerSqm)}/m²` : `${currencySymbol}/m² k.A.`}</span>
        ${item.priceValuation ? `<span class="valuation valuation-${item.priceValuation}">${VALUATION_LABELS[item.priceValuation]}</span>` : ""}
      </div>
      ${item.qualityNote ? `<div class="note">${item.qualityNote}; Geoposition: ${item.geoPrecision}</div>` : `<div class="note">Geoposition: ${item.geoPrecision}</div>`}
      <div class="actions">
        <span class="source">${item.source} · ${item.address}</span>
        <a class="open-link" href="${item.url}" target="_blank" rel="noreferrer">Inserat öffnen</a>
      </div>
    </article>
  `).join("");

  results.querySelectorAll(".card").forEach((card) => {
    card.addEventListener("click", () => {
      selectedId = card.dataset.cardId;
      render();
    });
  });
}

function applyModeChrome() {
  const nl = mode === "nl";
  eyebrow.textContent = nl
    ? "Wasserlage – Katwijk, Rijnsburg & Valkenburg"
    : "Kaufobjekte rund um Kreuzlingen";
  mapHeading.textContent = nl
    ? "Lage an der Oude Rijn (Rijnsburg)"
    : `Lage zu ${referenceFor("ch")?.label ?? "Referenzpunkt"}`;
  if (priceSwitch) priceSwitch.hidden = nl;
  document.querySelectorAll("[data-ch-only]").forEach((el) => {
    el.hidden = nl;
  });
  document.querySelectorAll("[data-nl-only]").forEach((el) => {
    el.hidden = !nl;
  });
}

function renderSummary(items) {
  const criteria = payload.criteria[mode] || {};
  resultCount.textContent = `${items.length} ${items.length === 1 ? "Objekt" : "Objekte"}`;
  const stats = payload.priceStats?.[mode];
  if (mode === "nl") {
    closestLabel.textContent = items[0] ? `Top-Score: ${items[0].nlScore ?? 0}` : "-";
    medianLabel.textContent = stats
      ? `Median € ${new Intl.NumberFormat("de-CH").format(stats.medianPpsqm)}/m²`
      : "";
    filterNote.textContent =
      "Harte Filter: Wasserlage (Steiger oder Wunsch-Strasse), keine Recreatiewoningen, 4+ Zimmer, kein Preislimit; sortiert nach Lage-/Zustands-Score.";
  } else {
    closestLabel.textContent = items[0] ? `Nächstes: ${formatNumber(items[0].distanceKm, 1)} km` : "-";
    medianLabel.textContent = stats
      ? `Median CHF ${new Intl.NumberFormat("de-CH").format(stats.medianPpsqm)}/m²`
      : "";
    const modeTotal = listingsForTab(mode).length;
    const hidden = modeTotal - items.length;
    priceToggleLabel.textContent = `Nur bis ${criteria.priceLimitLabel}`;
    filterNote.textContent = priceToggle.checked
      ? `${hidden} Objekt(e) wegen Preis > ${criteria.priceLimitLabel} oder "Auf Anfrage" ausgeblendet.`
      : `Alle passenden ${criteria.label.toLowerCase()}-Objekte inklusive "Auf Anfrage" und über ${criteria.priceLimitLabel}.`;
  }
  modeLinks.forEach((link) => {
    link.classList.toggle("active", link.dataset.modeLink === mode);
  });
}

function render() {
  const items = visibleListings();
  applyModeChrome();
  renderSummary(items);
  drawMap(items);
  renderCards(items);
  updateBadges();
}

function switchMode(nextMode, pushHistory) {
  mode = nextMode;
  selectedId = null;
  if (pushHistory) {
    window.history.pushState({}, "", MODE_PATHS[mode] || "/");
  }
  markTabSeen(mode);
  if (mode === "nl") {
    loadNlStreets();
  }
  render();
}

// The private overlay endpoint answers first; the committed file is the
// fallback so the page also works locally and if the function is unavailable.
async function loadPayload() {
  try {
    const response = await fetch("api/listings", { cache: "no-store" });
    const type = response.headers.get("content-type") || "";
    if (response.ok && type.includes("application/json")) {
      return await response.json();
    }
  } catch (error) {
    // fall through to the public file
  }
  const fallback = await fetch("assets/listings.json", { cache: "no-store" });
  return fallback.json();
}

async function init() {
  payload = await loadPayload();
  updatedAt.textContent = `Stand ${formatDate(payload.generatedAt)} · ${payload.source}`;
  priceToggle.addEventListener("change", () => {
    selectedId = null;
    render();
  });
  resetSelection.addEventListener("click", () => {
    selectedId = null;
    render();
  });
  modeLinks.forEach((link) => {
    link.addEventListener("click", (event) => {
      event.preventDefault();
      switchMode(link.dataset.modeLink, true);
    });
  });
  window.addEventListener("popstate", () => {
    switchMode(modeFromPath(), false);
  });

  ensureMap();
  if (schoolToggle) {
    schoolToggle.addEventListener("change", applyOverlayLayers);
  }
  if (nlStreetToggle) {
    nlStreetToggle.addEventListener("change", applyOverlayLayers);
  }
  markTabSeen(mode);
  if (mode === "nl") {
    await loadNlStreets();
  }
  render();
  await loadSchools();
  applyOverlayLayers();
}

init().catch((error) => {
  console.error(error);
  updatedAt.textContent = "Daten konnten nicht geladen werden";
  results.innerHTML = `<div class="card"><h3>Daten konnten nicht geladen werden</h3><p class="filter-note">Prüfe, ob assets/listings.json erzeugt wurde.</p></div>`;
});
