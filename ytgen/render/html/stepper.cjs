// Frame-stepping HTML renderer: loads a page that exposes window.renderAt(t),
// screenshots every frame and pipes PNGs into ffmpeg. Deterministic: the page
// draws exactly the requested time, no wall clock involved.
// usage: node stepper.cjs page.html out.mp4 width height fps seconds
const { chromium } = require('playwright');
const { spawn } = require('child_process');
(async () => {
  const [page, out, W, H, fps, secs] = process.argv.slice(2);
  const w = +W, h = +H, n = Math.max(1, Math.round(+fps * +secs));
  const browser = await chromium.launch({ args: ['--disable-gpu', '--force-color-profile=srgb'] });
  const p = await browser.newPage({ viewport: { width: w, height: h }, deviceScaleFactor: 1 });
  await p.goto('file://' + page);
  await p.waitForFunction(() => typeof window.renderAt === 'function' && window.__ready !== false, null, { timeout: 30000 });
  const ff = spawn('ffmpeg', ['-v', 'error', '-y', '-f', 'image2pipe', '-framerate', String(fps), '-c:v', 'png', '-i', '-',
    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '15', '-pix_fmt', 'yuv420p', out], { stdio: ['pipe', 'inherit', 'inherit'] });
  for (let i = 0; i < n; i++) {
    await p.evaluate((t) => window.renderAt(t), i / +fps);
    const buf = await p.screenshot({ type: 'png' });
    if (!ff.stdin.write(buf)) await new Promise((r) => ff.stdin.once('drain', r));
  }
  ff.stdin.end();
  await new Promise((r) => ff.on('close', r));
  await browser.close();
})().catch((e) => { console.error(e); process.exit(1); });
