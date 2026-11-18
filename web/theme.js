'use strict';

// Loaded first on every page, so that the saved theme applies before anything is drawn.
const THEMES = [
  ['auto', 'Automatique', 'linear-gradient(135deg, #f9f9f7 50%, #1a1a19 50%)'],
  ['clair', 'Clair', '#fcfcfb'],
  ['sombre', 'Sombre', '#1a1a19'],
];

function applyTheme(name) {
  if (name === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.dataset.theme = name;
}

function savedTheme() {
  try {
    const saved = localStorage.getItem('meteo-theme');
    return THEMES.some(([name]) => name === saved) ? saved : 'auto';
  } catch {
    return 'auto';
  }
}

function saveTheme(name) {
  try {
    localStorage.setItem('meteo-theme', name);
  } catch {
    /* the choice just won't be remembered */
  }
}

applyTheme(savedTheme());
