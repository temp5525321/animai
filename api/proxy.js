export const config = {
  api: {
    responseLimit: false,
  },
};

export default async function handler(req, res) {
  const { url } = req.query;

  if (!url) {
    return res.status(400).json({ error: 'URL parameter required' });
  }

  const allowedDomains = ['mlbpark.donga.com', 'image.donga.com', 'simg.donga.com', 'donga.com'];
  let parsedUrl;
  try {
    parsedUrl = new URL(decodeURIComponent(url));
  } catch (e) {
    return res.status(400).json({ error: 'Invalid URL' });
  }

  const isAllowed = allowedDomains.some(d => parsedUrl.hostname.endsWith(d));
  if (!isAllowed) {
    return res.status(403).json({ error: 'Domain not allowed' });
  }

  try {
    const upstreamHeaders = {
      'Referer': 'https://mlbpark.donga.com/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
      'Accept': '*/*',
      'Accept-Language': 'ko-KR,ko;q=0.9',
    };

    // Range 헤더 전달 (seek 지원)
    if (req.headers.range) {
      upstreamHeaders['Range'] = req.headers.range;
    }

    const response = await fetch(parsedUrl.toString(), { headers: upstreamHeaders });

    if (!response.ok && response.status !== 206) {
      return res.status(response.status).json({ error: 'Upstream error' });
    }

    const contentType = response.headers.get('content-type') || 'video/mp4';
    const contentLength = response.headers.get('content-length');
    const contentRange = response.headers.get('content-range');
    const acceptRanges = response.headers.get('accept-ranges');

    res.setHeader('Content-Type', contentType);
    res.setHeader('Access-Control-Allow-Origin', '*');
    res.setHeader('Cache-Control', 'public, max-age=3600');
    res.setHeader('Accept-Ranges', acceptRanges || 'bytes');
    if (contentLength) res.setHeader('Content-Length', contentLength);
    if (contentRange) res.setHeader('Content-Range', contentRange);

    res.status(response.status);

    const reader = response.body.getReader();
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      res.write(Buffer.from(value));
    }
    res.end();

  } catch (error) {
    console.error('Proxy error:', error);
    return res.status(500).json({ error: 'Proxy failed' });
  }
}
