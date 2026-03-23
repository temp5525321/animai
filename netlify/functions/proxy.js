exports.handler = async function(event, context) {
  const url = event.queryStringParameters?.url;

  if (!url) {
    return { statusCode: 400, body: JSON.stringify({ error: 'URL parameter required' }) };
  }

  const allowedDomains = ['mlbpark.donga.com', 'image.donga.com', 'simg.donga.com', 'donga.com', 'tpzlfh.uk', 'mlbpark.tpzlfh.uk', 'twitter.com', 'x.com', 'twimg.com', 'video.twimg.com', 'pbs.twimg.com'];
  let parsedUrl;
  try {
    parsedUrl = new URL(decodeURIComponent(url));
  } catch (e) {
    return { statusCode: 400, body: JSON.stringify({ error: 'Invalid URL' }) };
  }

  const isAllowed = allowedDomains.some(d => parsedUrl.hostname.endsWith(d));
  if (!isAllowed) {
    return { statusCode: 403, body: JSON.stringify({ error: 'Domain not allowed' }) };
  }

  const isXVideo = parsedUrl.hostname.includes('twitter.com') || parsedUrl.hostname.includes('x.com') || parsedUrl.hostname.includes('twimg.com');

  try {
    const upstreamHeaders = {
      'Referer': isXVideo ? 'https://twitter.com/' : 'https://mlbpark.donga.com/',
      'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
      'Accept': '*/*',
      'Accept-Language': 'ko-KR,ko;q=0.9',
      'Origin': isXVideo ? 'https://twitter.com' : 'https://mlbpark.donga.com',
    };

    if (event.headers?.range) {
      upstreamHeaders['Range'] = event.headers.range;
    }

    const response = await fetch(parsedUrl.toString(), { headers: upstreamHeaders });

    if (!response.ok && response.status !== 206) {
      return { statusCode: response.status, body: JSON.stringify({ error: 'Upstream error' }) };
    }

    const contentType = response.headers.get('content-type') || 'video/mp4';
    const contentLength = response.headers.get('content-length');
    const contentRange = response.headers.get('content-range');

    const buffer = await response.arrayBuffer();
    const base64 = Buffer.from(buffer).toString('base64');

    const headers = {
      'Content-Type': contentType,
      'Access-Control-Allow-Origin': '*',
      'Cache-Control': 'public, max-age=3600, s-maxage=3600, stale-while-revalidate=86400',
    };
    if (contentLength) headers['Content-Length'] = contentLength;
    if (contentRange) headers['Content-Range'] = contentRange;

    return {
      statusCode: response.status,
      headers,
      body: base64,
      isBase64Encoded: true,
    };

  } catch (error) {
    console.error('Proxy error:', error);
    return { statusCode: 500, body: JSON.stringify({ error: 'Proxy failed' }) };
  }
};
