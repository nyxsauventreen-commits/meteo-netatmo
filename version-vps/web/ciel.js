/* Ciel animé de la page de connexion : il suit l'heure de la personne.
   Nuit : étoiles qui scintillent, lune, parfois une étoile filante. Aube et coucher : ciel orangé, soleil bas.
   Jour : soleil et nuages qui passent. */
(() => {
  'use strict';
  const cv = document.getElementById('ciel'); if (!cv) return;
  const cx = cv.getContext('2d');
  const calme = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const h = new Date().getHours() + new Date().getMinutes() / 60;
  const force = new URLSearchParams(location.search).get('ciel');   // ?ciel=nuit|aube|jour|soir pour voir les autres ciels
  const moment = ['nuit', 'aube', 'jour', 'soir'].includes(force) ? force : h < 6 || h >= 21 ? 'nuit' : h < 8 ? 'aube' : h < 18 ? 'jour' : 'soir';
  const CIELS = {
    nuit: ['#070b1d', '#111a3d', '#24305e'],
    aube: ['#27325f', '#a26a8e', '#f6ae78'],
    jour: ['#2f7fd8', '#5aa6ec', '#a9d7ff'],
    soir: ['#2a2457', '#b0577a', '#f59a5c'],
  };
  const salut = document.getElementById('salut');
  if (salut) {
    const mots = { nuit: 'Bonne nuit 🌙', aube: 'Bon matin 🌅', jour: 'Bonjour ☀️', soir: 'Bonsoir 🌇' };
    const heure = () => new Date().toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' });
    salut.textContent = `${mots[moment]} · ${heure()}`;
    setInterval(() => { salut.textContent = `${mots[moment]} · ${heure()}`; }, 20000);
  }
  const meta = document.querySelector('meta[name="theme-color"]'); if (meta) meta.content = CIELS[moment][0];

  let L = 0, H = 0, etoiles = [], nuages = [], filante = null;
  // Un nuage : des boules blanches dessinées une fois sur une petite toile, puis déplacées
  function faireNuage(echelle) {
    const w = 260 * echelle, hh = 110 * echelle, c = document.createElement('canvas');
    c.width = w; c.height = hh; const g = c.getContext('2d');
    const boules = 6 + Math.floor(Math.random() * 4);
    for (let i = 0; i < boules; i++) {
      const x = w * (.15 + .7 * i / (boules - 1)) + (Math.random() - .5) * 20 * echelle;
      const r = (26 + Math.random() * 26) * echelle * (1 - Math.abs(i / (boules - 1) - .5) * .7);
      const y = hh * .62 - r * .35 + (Math.random() - .5) * 8 * echelle;
      const gr = g.createRadialGradient(x, y - r * .3, r * .2, x, y, r);
      gr.addColorStop(0, '#ffffff'); gr.addColorStop(1, '#e3ecf7');
      g.fillStyle = gr; g.beginPath(); g.arc(x, y, r, 0, 6.283); g.fill();
    }
    g.fillStyle = '#e8f0fa'; g.beginPath(); g.ellipse(w * .5, hh * .7, w * .36, hh * .13, 0, 0, 6.283); g.fill();
    return c;
  }
  function taille() {
    const r = Math.min(devicePixelRatio || 1, 2); L = innerWidth; H = innerHeight;
    cv.width = L * r; cv.height = H * r; cx.setTransform(r, 0, 0, r, 0, 0);
    const nbEtoiles = moment === 'nuit' ? Math.round(L * H / 3500) : moment === 'jour' ? 0 : Math.round(L * H / 14000);
    etoiles = Array.from({ length: Math.min(nbEtoiles, 500) }, () => ({ x: Math.random() * L, y: Math.random() * H * .8, r: Math.random() * 1.3 + .2, p: Math.random() * 6.28, v: .5 + Math.random() * 2 }));
    const nbNuages = moment === 'jour' ? 7 : moment === 'nuit' ? 4 : 5;
    nuages = Array.from({ length: nbNuages }, (_, i) => {
      const e = .5 + Math.random() * .9;
      return { img: faireNuage(e), x: Math.random() * (L + 400) - 200, y: H * (.06 + .62 * i / nbNuages) + Math.random() * 40, v: .08 + e * .22 };
    });
  }
  function astre(t) {
    if (moment === 'jour' || moment === 'aube' || moment === 'soir') {
      // Soleil : haut à droite le jour, bas sur l'horizon à l'aube et le soir
      const x = moment === 'aube' ? L * .22 : moment === 'soir' ? L * .78 : L * .8, y = moment === 'jour' ? H * .18 : H * .72;
      const pulse = 1 + Math.sin(t / 1400) * .03, R = Math.min(L, H) * (moment === 'jour' ? .06 : .09) * pulse;
      const halo = cx.createRadialGradient(x, y, R * .5, x, y, R * 6);
      halo.addColorStop(0, moment === 'jour' ? 'rgba(255,244,200,.55)' : 'rgba(255,190,120,.6)'); halo.addColorStop(1, 'rgba(255,220,150,0)');
      cx.fillStyle = halo; cx.fillRect(0, 0, L, H);
      const disque = cx.createRadialGradient(x, y, R * .2, x, y, R);
      disque.addColorStop(0, moment === 'jour' ? '#fff8d6' : '#ffe2b0'); disque.addColorStop(1, moment === 'jour' ? '#ffd45e' : '#ff8f4f');
      cx.fillStyle = disque; cx.beginPath(); cx.arc(x, y, R, 0, 6.283); cx.fill();
    } else {
      // Lune
      const x = L * .78, y = H * .2, R = Math.min(L, H) * .055;
      const halo = cx.createRadialGradient(x, y, R, x, y, R * 5);
      halo.addColorStop(0, 'rgba(200,215,255,.28)'); halo.addColorStop(1, 'rgba(200,215,255,0)');
      cx.fillStyle = halo; cx.fillRect(0, 0, L, H);
      const disque = cx.createRadialGradient(x - R * .35, y - R * .35, R * .1, x, y, R);
      disque.addColorStop(0, '#fbfcff'); disque.addColorStop(1, '#c9d3ea');
      cx.fillStyle = disque; cx.beginPath(); cx.arc(x, y, R, 0, 6.283); cx.fill();
      cx.fillStyle = 'rgba(150,165,200,.35)';
      for (const [dx, dy, rr] of [[-.3, -.1, .18], [.25, .2, .13], [.05, -.4, .1], [-.1, .35, .09]]) { cx.beginPath(); cx.arc(x + dx * R, y + dy * R, rr * R, 0, 6.283); cx.fill(); }
    }
  }
  let avant = performance.now();
  function dessiner(t) {
    const dt = Math.min(50, t - avant); avant = t;
    const c = CIELS[moment], fond = cx.createLinearGradient(0, 0, 0, H);
    fond.addColorStop(0, c[0]); fond.addColorStop(.55, c[1]); fond.addColorStop(1, c[2]);
    cx.fillStyle = fond; cx.fillRect(0, 0, L, H);
    for (const e of etoiles) {
      cx.globalAlpha = .35 + .65 * (.5 + .5 * Math.sin(e.p + t / 1000 * e.v));
      cx.fillStyle = '#fff'; cx.beginPath(); cx.arc(e.x, e.y, e.r, 0, 6.283); cx.fill();
    }
    cx.globalAlpha = 1;
    // Étoile filante, de temps en temps la nuit
    if (moment === 'nuit' && !calme) {
      if (!filante && Math.random() < dt / 9000) filante = { x: Math.random() * L * .6, y: Math.random() * H * .3, vie: 0 };
      if (filante) {
        filante.vie += dt; const k = filante.vie / 900, x = filante.x + k * 380, y = filante.y + k * 160;
        const tr = cx.createLinearGradient(x - 110, y - 46, x, y);
        tr.addColorStop(0, 'rgba(255,255,255,0)'); tr.addColorStop(1, `rgba(255,255,255,${.9 * (1 - k)})`);
        cx.strokeStyle = tr; cx.lineWidth = 2; cx.beginPath(); cx.moveTo(x - 110, y - 46); cx.lineTo(x, y); cx.stroke();
        if (k >= 1) filante = null;
      }
    }
    astre(t);
    // Nuages (plus sombres et discrets la nuit)
    cx.globalAlpha = moment === 'nuit' ? .16 : moment === 'jour' ? .9 : .55;
    for (const n of nuages) {
      n.x += n.v * dt / 16;
      if (n.x > L + 50) { n.x = -n.img.width - Math.random() * 200; }
      cx.drawImage(n.img, n.x, n.y);
    }
    cx.globalAlpha = 1;
    if (moment === 'aube' || moment === 'soir') {   // une brume douce sur l'horizon
      const brume = cx.createLinearGradient(0, H * .7, 0, H);
      brume.addColorStop(0, 'rgba(255,170,120,0)'); brume.addColorStop(1, 'rgba(255,170,120,.25)');
      cx.fillStyle = brume; cx.fillRect(0, H * .7, L, H * .3);
    }
    if (!calme) requestAnimationFrame(dessiner);
  }
  taille(); addEventListener('resize', taille);
  requestAnimationFrame(dessiner);
})();
