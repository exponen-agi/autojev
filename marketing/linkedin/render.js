// Renders video.html to MP4 (1080x1080, 30fps) by capturing frames deterministically.
const { chromium } = require('/opt/node-tools/node_modules/playwright');
const { execFileSync } = require('child_process');
const fs = require('fs'), path = require('path');
const [,, html, out, frames] = process.argv;
(async () => {
  fs.mkdirSync(frames, { recursive: true });
  const b = await chromium.launch({ executablePath: '/opt/pw-browsers/chromium' });
  const p = await b.newPage({ viewport: { width: 1080, height: 1080 } });
  await p.goto('file://' + path.resolve(html));
  const fps = 30, dur = await p.evaluate(() => window.DUR);
  for (let i = 0; i < fps * dur; i++) {
    await p.evaluate(t => window.render(t), i / fps);
    await p.screenshot({ path: `${frames}/f${String(i).padStart(4, '0')}.png` });
  }
  await b.close();
  execFileSync('ffmpeg', ['-y','-framerate',String(fps),'-i',`${frames}/f%04d.png`,'-c:v','libx264','-pix_fmt','yuv420p','-crf','18','-movflags','+faststart',out], { stdio: 'inherit' });
})();
