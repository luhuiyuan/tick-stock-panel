const http = require('http');
const fs = require('fs');
const path = require('path');

// 只读暴露成果目录，支持目录浏览 + HTTP Range 断点续传
const ROOT = process.env.TSP_DATA || '/workspace/tick-data';
const PORT = process.env.PORT || 8088;

const MIME = {
  '.zip': 'application/zip',
  '.csv': 'text/csv',
  '.gz': 'application/gzip',
  '.json': 'application/json',
  '.txt': 'text/plain',
  '.log': 'text/plain',
  '.md': 'text/markdown',
};

function listDir(dir, rel) {
  let entries;
  try {
    entries = fs.readdirSync(dir, { withFileTypes: true });
  } catch (e) {
    return '<p>无法读取目录</p>';
  }
  entries.sort((a, b) => {
    if (a.isDirectory() !== b.isDirectory()) return a.isDirectory() ? -1 : 1;
    return a.name.localeCompare(b.name);
  });
  let html = `<!doctype html><meta charset="utf-8"><h1>Index of /${rel}</h1><ul>`;
  if (rel) html += '<li><a href="../">../</a></li>';
  for (const e of entries) {
    const isDir = e.isDirectory();
    const name = e.name + (isDir ? '/' : '');
    let size = '';
    if (!isDir) {
      try { size = '  (' + (fs.statSync(path.join(dir, e.name)).size / 1e9).toFixed(2) + ' GB)'; } catch (_) {}
    }
    html += `<li><a href="${encodeURIComponent(e.name) + (isDir ? '/' : '')}">${name}</a>${size}</li>`;
  }
  html += '</ul>';
  return html;
}

const server = http.createServer((req, res) => {
  const urlPath = decodeURIComponent(req.url.split('?')[0]);
  const filePath = path.normalize(path.join(ROOT, urlPath));
  if (!filePath.startsWith(ROOT)) {
    res.writeHead(403); res.end('Forbidden'); return;
  }
  fs.stat(filePath, (err, stat) => {
    if (err) { res.writeHead(404); res.end('Not Found'); return; }
    if (stat.isDirectory()) {
      res.writeHead(200, { 'Content-Type': 'text/html; charset=utf-8' });
      res.end(listDir(filePath, urlPath.replace(/^\//, '')));
      return;
    }
    const total = stat.size;
    const ctype = MIME[path.extname(filePath).toLowerCase()] || 'application/octet-stream';
    const range = req.headers.range;
    if (range) {
      const m = /bytes=(\d*)-(\d*)/.exec(range);
      let start = m && m[1] ? parseInt(m[1], 10) : 0;
      let end = m && m[2] ? parseInt(m[2], 10) : total - 1;
      if (isNaN(start) || isNaN(end) || start > end || start >= total) {
        res.writeHead(416, { 'Content-Range': `bytes */${total}` }); res.end(); return;
      }
      res.writeHead(206, {
        'Content-Type': ctype,
        'Content-Length': end - start + 1,
        'Content-Range': `bytes ${start}-${end}/${total}`,
        'Accept-Ranges': 'bytes',
      });
      fs.createReadStream(filePath, { start, end }).pipe(res);
    } else {
      res.writeHead(200, {
        'Content-Type': ctype,
        'Content-Length': total,
        'Accept-Ranges': 'bytes',
      });
      fs.createReadStream(filePath).pipe(res);
    }
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`download server listening on ${PORT}, root=${ROOT}`);
});
