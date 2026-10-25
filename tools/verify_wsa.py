#!/usr/bin/env python3
"""元宝搜索 SearchPro 自检 / 最小客户端（纯标准库，零依赖）

支持两种鉴权，自动选择：
  1) 服务 API KEY（推荐）：Bearer sk-xxx     -> api.wsa.cloud.tencent.com/SearchPro
  2) 腾讯云 AK/SK：TC3-HMAC-SHA256 签名      -> wsa.tencentcloudapi.com

凭据读取顺序：环境变量 > 同目录/上级的 .env

用法：
    python3 tools/verify_wsa.py                    # 用默认测试查询
    python3 tools/verify_wsa.py "空气炸锅 涂层 脱落"
"""
import hashlib
import hmac
import json
import os
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

SERVICE = "wsa"
ACTION = "SearchPro"
VERSION = "2025-05-08"
REGION = ""  # 元宝搜索不支持 region，带 ap-guangzhou 会报 UnsupportedRegion

HOST_BEARER = "api.wsa.cloud.tencent.com"
HOST_TC3 = "wsa.tencentcloudapi.com"

DEFAULT_QUERY = "空气炸锅 不粘涂层 脱落 召回"

# 显式禁用系统代理：沙箱/企业网络注入的 HTTP_PROXY 会干扰直连
_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def load_env() -> None:
    """从 .env 读凭据（只补空缺，不覆盖已存在的环境变量）。"""
    for base in (Path(__file__).resolve().parent, Path(__file__).resolve().parent.parent):
        f = base / ".env"
        if not f.exists():
            continue
        for raw in f.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())
        break


def _sha256_hex(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode("utf-8"), hashlib.sha256).digest()


def _post(host: str, path: str, body: str, headers: dict) -> dict:
    req = urllib.request.Request(
        f"https://{host}{path}", data=body.encode("utf-8"), method="POST", headers=headers
    )
    with _OPENER.open(req, timeout=20) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _search_bearer(payload: dict, api_key: str) -> dict:
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    return _post(
        HOST_BEARER,
        "/SearchPro",
        body,
        {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json; charset=utf-8",
        },
    )


def _search_tc3(payload: dict, secret_id: str, secret_key: str) -> dict:
    ts = int(time.time())
    date = time.strftime("%Y-%m-%d", time.gmtime(ts))
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    content_type = "application/json; charset=utf-8"

    canonical_headers = f"content-type:{content_type}\nhost:{HOST_TC3}\n"
    signed_headers = "content-type;host"
    canonical_request = (
        f"POST\n/\n\n{canonical_headers}\n{signed_headers}\n{_sha256_hex(body)}"
    )
    scope = f"{date}/{SERVICE}/tc3_request"
    string_to_sign = f"TC3-HMAC-SHA256\n{ts}\n{scope}\n{_sha256_hex(canonical_request)}"

    k_date = _hmac(("TC3" + secret_key).encode("utf-8"), date)
    k_service = _hmac(k_date, SERVICE)
    k_signing = _hmac(k_service, "tc3_request")
    signature = hmac.new(
        k_signing, string_to_sign.encode("utf-8"), hashlib.sha256
    ).hexdigest()

    headers = {
        "Authorization": (
            f"TC3-HMAC-SHA256 Credential={secret_id}/{scope}, "
            f"SignedHeaders={signed_headers}, Signature={signature}"
        ),
        "Content-Type": content_type,
        "Host": HOST_TC3,
        "X-TC-Action": ACTION,
        "X-TC-Version": VERSION,
        "X-TC-Timestamp": str(ts),
    }
    if REGION:
        headers["X-TC-Region"] = REGION
    return _post(HOST_TC3, "/", body, headers)


def search(query, mode=2, cnt=10, freshness=None, industry=None, site=None):
    """对外的统一检索入口。返回 (pages, meta)。

    pages: list[dict]，字段 title / url / passage / site / date / score
    meta:  {"auth": "bearer"|"tc3", "elapsed": 秒, "request_id": str, "raw_count": int}
    """
    if cnt not in (10, 20, 30, 40, 50):
        raise ValueError("Cnt 只能取 10/20/30/40/50")
    payload = {"Query": query, "Mode": mode, "Cnt": cnt}
    if freshness:
        payload["Freshness"] = freshness
    if industry:
        payload["Industry"] = industry
    if site:
        payload["Site"] = site

    api_key = os.getenv("TENCENTCLOUD_WSA_APIKEY", "").strip()
    t0 = time.time()
    if api_key:
        data, auth = _search_bearer(payload, api_key), "bearer"
    else:
        sid = os.getenv("TENCENTCLOUD_SECRET_ID", "").strip()
        skey = os.getenv("TENCENTCLOUD_SECRET_KEY", "").strip()
        if not sid or not skey:
            raise RuntimeError(
                "没找到凭据：请设置 TENCENTCLOUD_WSA_APIKEY，或 TENCENTCLOUD_SECRET_ID + SECRET_KEY"
            )
        data, auth = _search_tc3(payload, sid, skey), "tc3"
    elapsed = time.time() - t0

    if not isinstance(data, dict) or not isinstance(data.get("Response"), dict):
        raise RuntimeError("SearchPro returned an invalid response envelope")
    resp = data["Response"]
    if resp.get("Error"):
        err = resp["Error"]
        raise RuntimeError(f"{err.get('Code')}: {err.get('Message')}")

    # SearchPro standard returns {RequestId, Query, Version} with no Pages for
    # an empty site-filtered search. Accept only this observed, correlated shape;
    # arbitrary missing fields, explicit null and wrong types remain failures.
    omitted_empty = (
        "Pages" not in resp and bool(resp.get("RequestId"))
        and resp.get("Query") == query and resp.get("Version") == "standard"
    )
    raw_pages = [] if omitted_empty else resp.get("Pages")
    if not isinstance(raw_pages, list):
        raise RuntimeError("SearchPro response is missing a Pages array")
    pages = []
    for p in raw_pages:
        try:
            parsed = json.loads(p) if isinstance(p, str) else p
            if not isinstance(parsed, dict):
                raise RuntimeError("SearchPro page is not an object")
            pages.append(parsed)
        except json.JSONDecodeError:
            raise RuntimeError("SearchPro page contains invalid JSON") from None

    return pages, {
        "auth": auth,
        "elapsed": round(elapsed, 2),
        "request_id": resp.get("RequestId"),
        "raw_count": len(pages),
        "result_status": "results" if pages else "empty",
        "empty_encoding": "pages_omitted" if omitted_empty else None,
    }


def main() -> int:
    load_env()
    query = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_QUERY
    print(f"查询：{query}")
    try:
        pages, meta = search(query)
    except Exception as e:  # noqa: BLE001
        print(f"[FAIL] {type(e).__name__}: {e}")
        return 1

    print(f"[OK] 鉴权={meta['auth']}  耗时={meta['elapsed']}s  "
          f"返回={meta['raw_count']}条  RequestId={meta['request_id']}")
    print("-" * 64)
    for i, it in enumerate(pages[:5], 1):
        print(f"[{i}] {it.get('title', '无标题')}")
        print(f"    来源: {it.get('site', '?')} | 时间: {it.get('date') or '未知'}")
        print(f"    链接: {it.get('url', '?')}")
        passage = (it.get("passage") or "").replace("\n", " ")
        print(f"    摘句: {passage[:110]}")
        print("-" * 64)
    if not pages:
        print("返回 0 条（注意：0 条也扣 1 次额度）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
