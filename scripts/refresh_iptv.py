from __future__ import annotations

import json
import re
import urllib.request
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

SOURCE = "https://gongdian.top/tv/iptv"
CONVERTER = "https://tmxk.pp.ua/chanshift/"
ACCEL = "https://gh-proxy.com/raw.githubusercontent.com/Zero-Hub/iptv/refs/heads/main/live.txt"
RAW = "https://raw.githubusercontent.com/Zero-Hub/iptv/refs/heads/main/live.txt"
AUTO_RE = re.compile(r"<!-- IPTV-AUTO-START -->.*?<!-- IPTV-AUTO-END -->", re.S)
VALID_URL = re.compile(r"^(?:https?|rtmps?|rtsp|udp|rtp)://", re.I)
SKIP_GROUP = re.compile(r"QQ群|广告|推广|联系|扫码")


def post_form(url: str, fields: dict[str, str]) -> dict:
    boundary = "----LobeIPTVBoundary7MA4YWxk"
    body = bytearray()
    for key, value in fields.items():
        body.extend(f"--{boundary}\r\n".encode())
        body.extend(f'Content-Disposition: form-data; name="{key}"\r\n\r\n'.encode())
        body.extend(value.encode("utf-8"))
        body.extend(b"\r\n")
    body.extend(f"--{boundary}--\r\n".encode())
    req = urllib.request.Request(
        url,
        data=bytes(body),
        headers={
            "Content-Type": f"multipart/form-data; boundary={boundary}",
            "User-Agent": "Mozilla/5.0 TVBox-Refresh-Agent/1.1",
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=120) as response:
        return json.loads(response.read().decode("utf-8"))


def canonical_group(name: str) -> str:
    return {"中数传媒": "数字频道", "轮播": "轮播频道"}.get(
        name.strip(), name.strip() or "其他频道"
    )


def clean_txt(text: str):
    # TVBox TXT parser groups multiple lines by channel name, but some forks only
    # keep the final duplicate line. Emit one line per channel and join routes
    # with #, which is explicitly supported by TVBox TxtSubscribe.parseTxt().
    groups: OrderedDict[str, OrderedDict[str, list[str]]] = OrderedDict()
    current = "其他频道"
    seen_urls: set[str] = set()
    duplicate_count = invalid_count = skipped_count = 0

    for raw_line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw_line.replace("\ufeff", "").strip()
        if not line:
            continue
        if line.endswith(",#genre#"):
            current = canonical_group(line[:-8])
            groups.setdefault(current, OrderedDict())
            continue
        if SKIP_GROUP.search(current):
            skipped_count += 1
            continue
        line = line.replace("，", ",", 1)
        if "," not in line:
            invalid_count += 1
            continue
        name, url = line.split(",", 1)
        name, url = name.strip(), url.strip().strip("<>")
        if not name or not VALID_URL.match(url):
            invalid_count += 1
            continue
        if url in seen_urls:
            duplicate_count += 1
            continue
        seen_urls.add(url)
        channels = groups.setdefault(current, OrderedDict())
        channels.setdefault(name, []).append(url)

    groups = OrderedDict(
        (group, channels) for group, channels in groups.items() if channels
    )
    lines: list[str] = []
    for group, channels in groups.items():
        lines.append(f"{group},#genre#")
        lines.extend(f"{name},{'#'.join(urls)}" for name, urls in channels.items())

    route_count = sum(len(urls) for channels in groups.values() for urls in channels.values())
    channel_count = sum(len(channels) for channels in groups.values())
    multi_channel_count = sum(
        1 for channels in groups.values() for urls in channels.values() if len(urls) > 1
    )
    return (
        "\n".join(lines) + "\n",
        groups,
        channel_count,
        route_count,
        multi_channel_count,
        duplicate_count,
        invalid_count,
        skipped_count,
    )


def build_auto_readme(groups, channel_count: int, route_count: int, updated_at: str) -> str:
    table = "\n".join(
        f"| {group} | {len(channels)} | {sum(len(urls) for urls in channels.values())} |"
        for group, channels in groups.items()
    )
    return f"""<!-- IPTV-AUTO-START -->
# TVBox 直播源

主直播源文件：[`live.txt`](https://github.com/Zero-Hub/iptv/blob/main/live.txt)

## 🚀 加速访问地址（国内推荐）

[点击打开加速直播源]({ACCEL})

```text
{ACCEL}
```

## 🌐 原始 Raw 地址

[点击打开原始直播源]({RAW})

```text
{RAW}
```

## TVBox 配置示例

```json
{{
  "lives": [
    {{
      "name": "Zero IPTV",
      "type": 0,
      "url": "{ACCEL}"
    }}
  ]
}}
```

## 直播源概况

- 当前频道数：**{channel_count}**
- 可选线路总数：**{route_count}**
- 频道分类数：**{len(groups)}**
- 最后更新时间：**{updated_at}（北京时间，UTC+8）**
- 数据来源：`{SOURCE}`

同一频道的多个播放地址已合并到同一行，并使用 `#` 分隔，以便不同 TVBox 分支正确显示和切换线路。

## 频道分类统计

| 分类 | 频道数 | 线路数 |
|---|---:|---:|
{table}
| **合计** | **{channel_count}** | **{route_count}** |

## 使用说明

将上方加速地址填入 TVBox 的直播源配置即可。国内网络环境优先使用加速地址；如网络环境允许，也可使用原始 Raw 地址。

## 注意事项

- `gh-proxy.com` 是第三方代理服务，其稳定性和隐私策略取决于服务提供方。
- 仓库公开不代表所有上游节目源始终可访问，节目源可能受地区、运营商、授权或时效限制。
- 请确保直播源的维护和使用符合版权要求、服务条款及当地法律法规。
<!-- IPTV-AUTO-END -->"""


def main() -> None:
    fetched = post_form(CONVERTER + "?action=fetch", {"source": SOURCE})
    if not fetched.get("success") or not fetched.get("data"):
        raise RuntimeError(f"订阅读取/解密失败：{fetched.get('error', '无数据')}")

    converted = post_form(
        CONVERTER + "?action=convert",
        {
            "action": "convert",
            "format": "txt",
            "data": fetched["data"],
            "epg": "https://epg.v1.mk/fy.xml",
            "logo": "https://cdn.jsdelivr.net/gh/fanmingming/live@main/tv/",
        },
    )
    if not converted.get("success") or not converted.get("output"):
        raise RuntimeError(f"转 TXT 失败：{converted.get('error', '无数据')}")

    (
        live,
        groups,
        channel_count,
        route_count,
        multi_channel_count,
        duplicates,
        invalid,
        skipped,
    ) = clean_txt(converted["output"])
    if channel_count < 10 or route_count < channel_count:
        raise RuntimeError("清洗后的频道或线路数量异常，拒绝覆盖 live.txt")

    Path("live.txt").write_text(live, encoding="utf-8", newline="\n")
    now = datetime.now(ZoneInfo("Asia/Shanghai")).strftime("%Y-%m-%d %H:%M:%S")
    auto = build_auto_readme(groups, channel_count, route_count, now)
    readme_path = Path("README.md")
    old = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    new = AUTO_RE.sub(auto, old, count=1) if AUTO_RE.search(old) else auto + ("\n\n" + old if old else "\n")
    readme_path.write_text(new, encoding="utf-8", newline="\n")

    summary = {
        "channels": channel_count,
        "routes": route_count,
        "multi_route_channels": multi_channel_count,
        "groups": {
            group: {
                "channels": len(channels),
                "routes": sum(len(urls) for urls in channels.values()),
            }
            for group, channels in groups.items()
        },
        "duplicates_removed": duplicates,
        "invalid_skipped": invalid,
        "promotional_skipped": skipped,
        "updated_at": now,
    }
    Path("refresh-result.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
