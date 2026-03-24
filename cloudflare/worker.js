export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);
    const targetUrl = url.searchParams.get('url');

    if (!targetUrl) {
      return new Response(JSON.stringify({ error: 'URL parameter required' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    let parsedUrl;
    try {
      parsedUrl = new URL(decodeURIComponent(targetUrl));
    } catch (e) {
      return new Response(JSON.stringify({ error: 'Invalid URL' }), {
        status: 400,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    const allowedDomains = [
      'mlbpark.donga.com', 'image.donga.com', 'simg.donga.com',
      'donga.com', 'tpzlfh.uk', 'mlbpark.tpzlfh.uk'
    ];
    const isAllowed = allowedDomains.some(d => parsedUrl.hostname.endsWith(d));
    if (!isAllowed) {
      return new Response(JSON.stringify({ error: 'Domain not allowed' }), {
        status: 403,
        headers: { 'Content-Type': 'application/json' }
      });
    }

    const upstreamHeaders = {
      'Referer': 'https://mlbpark.donga.com/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
      'Accept': '*/*',
      'Accept-Language': 'ko-KR,ko;q=0.9',
      'Origin': 'https://mlbpark.donga.com',
    };

    // Range 헤더 전달 (seek 지원)
    const range = request.headers.get('Range');
    if (range) upstreamHeaders['Range'] = range;

    try {
      const response = await fetch(parsedUrl.toString(), {
        headers: upstreamHeaders
      });

      const responseHeaders = new Headers();
      responseHeaders.set('Access-Control-Allow-Origin', '*');
      responseHeaders.set('Cache-Control', 'public, max-age=3600');

      const contentType = response.headers.get('content-type');
      const contentLength = response.headers.get('content-length');
      const contentRange = response.headers.get('content-range');
      const acceptRanges = response.headers.get('accept-ranges');

      if (contentType) responseHeaders.set('Content-Type', contentType);
      if (contentLength) responseHeaders.set('Content-Length', contentLength);
      if (contentRange) responseHeaders.set('Content-Range', contentRange);
      if (acceptRanges) responseHeaders.set('Accept-Ranges', acceptRanges);
      responseHeaders.set('Accept-Ranges', 'bytes');

      return new Response(response.body, {
        status: response.status,
        headers: responseHeaders
      });

    } catch (error) {
      return new Response(JSON.stringify({ error: 'Proxy failed', detail: error.message }), {
        status: 500,
        headers: { 'Content-Type': 'application/json' }
      });
    }
  }
};

