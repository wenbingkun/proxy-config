// Replace only recommend_queries on the reviewed search endpoint; preserve sibling fields.
(function () {
  let result = {};
  try {
    if (!/^https:\/\/api\.zhihu\.com\/search\/recommend_query\/v2\?/i.test(String($request.url || ""))) {
      $done({}); return;
    }
    const text = $response.body;
    let changed = false;
    const document = JSON.parse(text, (key, value) => {
      if (key === "recommend_queries" && value !== null && typeof value === "object" &&
          Object.keys(value).length) { changed = true; return {}; }
      return value;
    });
    if (changed) {
      // JSON.parse cannot retain large numeric IDs. Prefer passthrough to corrupting data.
      const tokens = text.match(/"(?:\\.|[^"\\])*"|-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/g) || [];
      if (tokens.some(token => token[0] !== '"' && Number.isInteger(Number(token)) && !Number.isSafeInteger(Number(token)))) {
        console.log("[Zhihu] Unsafe numeric ID; response unchanged");
      } else result = {body: JSON.stringify(document)};
    }
  } catch (error) {
    console.log("[Zhihu] Invalid recommendation JSON; response unchanged");
  }
  $done(result);
})();
