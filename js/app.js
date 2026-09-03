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
