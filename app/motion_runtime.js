// Measured pixels place the visuals; GSAP owns their entrance/exit poses.
window.clipkitMotionLayout = (visual, layout) => {
  const root = document.getElementById('root');
  for (const node of [document.documentElement, document.body]) {
    node.style.width = `${layout.width}px`; node.style.height = `${layout.height}px`;
  }
  root.dataset.width = layout.width; root.dataset.height = layout.height;
  visual.style.height = '100%'; visual.style.fontFamily = `"${layout.font_face}"`;
  const css = value => value.replace(/(-?\d+(?:\.\d+)?)px/g, (_, px) => `${+px * layout.scale}px`);
  for (const node of visual.querySelectorAll('.key,.sub,.card')) {
    node.style.left = `${540 * layout.scale + layout.left}px`;
    node.style.top = `${parseFloat(getComputedStyle(node).top) * layout.scale + layout.top}px`;
  }
  for (const node of visual.querySelectorAll('.card,.key,.glow,.sub,.unit,.bar,.cur')) {
    const style = getComputedStyle(node);
    for (const key of ['paddingTop','paddingRight','paddingBottom','paddingLeft','marginTop','marginRight',
      'marginBottom','marginLeft','borderRadius','boxShadow','textShadow','webkitTextStrokeWidth','filter']) {
      node.style[key] = css(style[key]);
    }
    if (node.matches('.card,.cur')) node.style.width = css(style.width);
    if (node.matches('.bar')) node.style.height = css(style.height);
  }
  window.clipkitMotionScaleCss = css;
  let ready = false;
  const check = () => {
    if (!ready) return;
    for (const node of visual.querySelectorAll('[data-motion-box]')) {
      const r = node.getBoundingClientRect(), [x0, y0, x1, y1] = layout.bounds;
      if (r.left < x0 - 1 || r.top < y0 - 1 || r.right > x1 + 1 || r.bottom > y1 + 1) {
        throw new Error('Motion exceeds its measured placement; shorten text or enlarge its area');
      }
    }
  };
  window.__clipkitMotionReady = document.fonts.load(`700 20px "${layout.font_face}"`).then(faces => {
    if (!faces.length || faces.some(face => face.status !== 'loaded')) throw new Error('Measured motion font did not load');
    ready = true; check();
  });
  window.__clipkitMotionReady.catch(error => { setTimeout(() => { throw error; }, 0); });
  window.clipkitMotionValidate = check;
  return check;
};
