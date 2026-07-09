const ARCA_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36';

// 아카 상세 페이지에서 본문 영상의 신선한 서명 URL을 추출.
// namu.la CDN 서명 URL은 1시간 만료 + Referer 핫링크 차단이라, 재생 시점마다 재추출한다.
async function resolveArca(articleId) {
  const pageUrl = `https://arca.live/b/aireal/${articleId}`;
  const res = await fetch(pageUrl, {
    headers: {
      'User-Agent': ARCA_UA,
      'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
      'Accept-Language': 'ko-KR,ko;q=0.9',
      'Referer': 'https://arca.live/b/aireal',
    },
  });
  if (!res.ok) return null;
  const html = await res.text();

  // 본문 영상 (namu.la mp4) src / poster 추출
  const videoMatch = html.match(/src="(\/\/[a-z0-9.-]*namu\.la\/[^"]+\.mp4[^"]*)"/i);
  const posterMatch = html.match(/poster="(\/\/[a-z0-9.-]*namu\.la\/[^"]+)"/i);

  const toAbs = (u) => (u ? (u.startsWith('//') ? 'https:' + u : u) : null);
  return {
    video: toAbs(videoMatch ? videoMatch[1] : null),
    poster: toAbs(posterMatch ? posterMatch[1] : null),
  };
}

function corsJson(obj, status = 200) {
  return new Response(JSON.stringify(obj), {
    status,
    headers: {
      'Content-Type': 'application/json',
      'Access-Control-Allow-Origin': '*',
    },
  });
}

export default {
  async fetch(request, env, ctx) {
    const url = new URL(request.url);

    // ── 아카 영상 재생: ?arca=<글번호> → 신선한 서명 URL로 302 redirect ──
    // 사이트에서 <video referrerpolicy="no-referrer">로 재생하면 namu.la 핫링크 차단을 우회.
    const arcaId = url.searchParams.get('arca');
    if (arcaId) {
      if (!/^\d+$/.test(arcaId)) return corsJson({ error: 'invalid id' }, 400);
      try {
        const resolved = await resolveArca(arcaId);
        if (!resolved || !resolved.video) return corsJson({ error: 'video not found' }, 404);

        // format=json이면 URL만 반환 (사이트가 직접 src 지정 가능)
        if (url.searchParams.get('format') === 'json') {
          return corsJson(resolved);
        }
        return new Response(null, {
          status: 302,
          headers: {
            'Location': resolved.video,
            'Referrer-Policy': 'no-referrer',
            'Access-Control-Allow-Origin': '*',
            'Cache-Control': 'no-store',
          },
        });
      } catch (e) {
        return corsJson({ error: 'resolve failed', detail: e.message }, 500);
      }
    }

    // ── 아카 썸네일: ?arcaThumb=<글번호> → poster 이미지 스트림 + 엣지 캐싱 ──
    const thumbId = url.searchParams.get('arcaThumb');
    if (thumbId) {
      if (!/^\d+$/.test(thumbId)) return corsJson({ error: 'invalid id' }, 400);
      const cache = caches.default;
      const cacheKey = new Request(url.toString(), request);
      const cached = await cache.match(cacheKey);
      if (cached) return cached;

      try {
        const resolved = await resolveArca(thumbId);
        if (!resolved || !resolved.poster) return corsJson({ error: 'thumb not found' }, 404);

        const imgRes = await fetch(resolved.poster, { headers: { 'User-Agent': ARCA_UA } });
        if (!imgRes.ok) return corsJson({ error: 'thumb fetch failed' }, 502);

        const headers = new Headers();
        headers.set('Content-Type', imgRes.headers.get('content-type') || 'image/webp');
        headers.set('Access-Control-Allow-Origin', '*');
        // poster 내용은 글별로 불변이므로 길게 캐싱 (서명 URL만 바뀔 뿐)
        headers.set('Cache-Control', 'public, max-age=604800');

        const out = new Response(imgRes.body, { status: 200, headers });
        ctx.waitUntil(cache.put(cacheKey, out.clone()));
        return out;
      } catch (e) {
        return corsJson({ error: 'thumb resolve failed', detail: e.message }, 500);
      }
    }

    // ── 기존 MLB 영상/이미지 프록시: ?url=<대상 URL> ──
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
