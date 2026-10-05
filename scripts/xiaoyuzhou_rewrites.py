"""Keep XiaoYuZhou's normal features when freezing fmz200's native rewrites."""

PREFIX = r"^https?:\/\/api\.xiaoyuzhoufm\.com\/"
DISCOVERY_JQ = '.data |= map(select(.type != "DISCOVERY_BANNER"))'


def preserve_features(text: str) -> str:
    # Match the reviewed source exactly: a source update needs another audit rather than
    # silently retaining a new broad reject or response that clears normal recommendations.
    replacements = {
        PREFIX + r"v\d\/flash": PREFIX + r"v\d\/flash-screen\/list(\?|$)",
        PREFIX + r"v\d\/ai": None,
        PREFIX + r"v\d\/search\/get": PREFIX + r"v\d\/search\/(get-express|get-preset)(\?|$)",
        PREFIX + r"v\d\/category": PREFIX + r"v\d\/category\/list-daily-suggestion(\?|$)",
        PREFIX + r"v1\/(related-episode|operation-resource)\/list": None,
        PREFIX + r"v1\/discovery-feed\/list": PREFIX + r"v\d\/discovery-feed\/list",
    }
    lines = text.splitlines()
    for old, new in replacements.items():
        matches = [i for i, line in enumerate(lines) if not line.lstrip().startswith("#") and old in line.split()]
        if len(matches) != 1:
            raise ValueError(f"XiaoYuZhou: expected exactly one source rule for {old}")
        i = matches[0]
        if new is None:
            lines[i] = ""
        else:
            lines[i] = lines[i].replace(old, new, 1)
            if "discovery-feed" in old:
                action, quote, expression = lines[i].partition("'")
                if not quote or not expression.endswith("'"):
                    raise ValueError("XiaoYuZhou: expected a quoted discovery jq expression")
                lines[i] = action + "'" + DISCOVERY_JQ + "'"
    return "\n".join(line for line in lines if line != "# 移除单集总结和ai总结") + "\n"
