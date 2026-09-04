// BGE Benchmarks - shared data, colour, and project (localStorage) logic.

const DATA_URL = "data/benchmarks.json";
const PROJECT_KEY = "bge_project_v1";

const PALETTE = [
  "#8b5cf6", "#34d399", "#60a5fa", "#fbbf24",
  "#f472b6", "#22d3ee", "#fb923c", "#a3e635",
  "#f87171", "#2dd4bf", "#818cf8", "#38bdf8"
];

const LEVEL_COLORS = { 0: "#34d399", 1: "#60a5fa", 2: "#a78bfa", 3: "#fbbf24", 4: "#f87171" };

function hashString(str) {
  let h = 0;
  for (let i = 0; i < str.length; i++) {
    h = (h * 31 + str.charCodeAt(i)) | 0;
  }
  return Math.abs(h);
}

function colorForOrganiser(organiser) {
  const idx = hashString(organiser || "") % PALETTE.length;
  return PALETTE[idx];
}

function colorForLevel(level) {
  return LEVEL_COLORS[level] || "#8b5cf6";
}

async function loadBenchmarks() {
  const res = await fetch(DATA_URL);
  if (!res.ok) throw new Error("Could not load benchmark data");
  return res.json();
}

function getProject() {
  try {
    return JSON.parse(localStorage.getItem(PROJECT_KEY) || "[]");
  } catch (e) {
    return [];
  }
}

function saveProject(ids) {
  localStorage.setItem(PROJECT_KEY, JSON.stringify(ids));
}

function isInProject(id) {
  return getProject().includes(id);
}

function toggleProject(id) {
  const ids = getProject();
  const i = ids.indexOf(id);
  if (i === -1) ids.push(id); else ids.splice(i, 1);
  saveProject(ids);
  updateProjectBadge();
  return ids.includes(id);
}

function removeFromProject(id) {
  saveProject(getProject().filter((x) => x !== id));
  updateProjectBadge();
}

function clearProject() {
  saveProject([]);
  updateProjectBadge();
}

function updateProjectBadge() {
  const el = document.querySelector("[data-project-count]");
  if (el) el.textContent = getProject().length;
}

// ---------- PDF-to-benchmark matching (used by scan.html) ----------
// Deliberately simple lexical (TF-IDF-style) matching, not semantic search -
// it finds benchmarks that share distinctive vocabulary with the uploaded
// document. Restricting to one curriculum area first (required by the UI)
// is what keeps this usable: it's the difference between "cyber resilience"
// only ever competing against other Technologies benchmarks, rather than
// against unrelated hits like HWB's "emotional resilience" content.
const MATCH_STOPWORDS = new Set(("a an the and or but of to in on for with is are was were be been being i you he "
  + "she it we they my your his her its our their this that these those can could will would shall should may "
  + "might must do does did have has had as at by from into about than then so if not no yes also which who whom "
  + "what when where why how all each other some such only own same more most use using used like get one two "
  + "three four five").split(" "));

function tokenize(text) {
  const matches = (text || "").toLowerCase().match(/[a-z][a-z'-]{2,}/g) || [];
  return matches.filter((w) => !MATCH_STOPWORDS.has(w));
}

function groupRowsByEo(rows) {
  const groups = new Map();
  for (const r of rows) {
    const key = r.eoCode + "||" + r.organiser;
    if (!groups.has(key)) groups.set(key, { eoCode: r.eoCode, organiser: r.organiser, eoText: r.eoText, rows: [] });
    groups.get(key).rows.push(r);
  }
  return [...groups.values()];
}

function groupText(g) {
  return `${g.organiser} ${g.eoText} ${g.rows.map((r) => r.benchmark).join(" ")}`;
}

function buildIdf(groups) {
  const df = new Map();
  groups.forEach((g) => {
    new Set(tokenize(groupText(g))).forEach((t) => df.set(t, (df.get(t) || 0) + 1));
  });
  const n = groups.length;
  const idf = new Map();
  df.forEach((count, term) => idf.set(term, Math.log((n + 1) / (count + 1)) + 1));
  return idf;
}

function scoreGroupsAgainstText(docText, rows) {
  const groups = groupRowsByEo(rows);
  const docFreq = new Map();
  tokenize(docText).forEach((t) => docFreq.set(t, (docFreq.get(t) || 0) + 1));
  const idf = buildIdf(groups);
  const scored = groups
    .map((g) => {
      let score = 0;
      new Set(tokenize(groupText(g))).forEach((t) => {
        if (docFreq.has(t)) score += (idf.get(t) || 1) * Math.min(docFreq.get(t), 5);
      });
      return { group: g, score };
    })
    .filter((s) => s.score > 0);
  scored.sort((a, b) => b.score - a.score);
  return scored;
}

/** Returns the picked EO groups: the organiser that scores highest overall
 * (every EO under it, however weakly it matched individually), plus any
 * other EO that independently scores within 60% of the top individual hit. */
function pickBestMatches(docText, rows) {
  const scored = scoreGroupsAgainstText(docText, rows);
  if (!scored.length) return [];
  const max = scored[0].score;
  const byOrganiser = new Map();
  scored.forEach((s) => byOrganiser.set(s.group.organiser, (byOrganiser.get(s.group.organiser) || 0) + s.score));
  const topOrganiser = [...byOrganiser.entries()].sort((a, b) => b[1] - a[1])[0][0];
  return scored
    .filter((s) => s.group.organiser === topOrganiser || s.score >= max * 0.6)
    .map((s) => s.group);
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  }[c]));
}

function highlight(text, query) {
  if (!query) return escapeHtml(text);
  const escaped = escapeHtml(text);
  const q = escapeHtml(query).replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  return escaped.replace(new RegExp(`(${q})`, "ig"), "<mark>$1</mark>");
}

document.addEventListener("DOMContentLoaded", updateProjectBadge);
