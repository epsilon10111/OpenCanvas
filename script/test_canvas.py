"""测试 Canvas REST API 连通性：读取当前用户 profile。"""

from __future__ import annotations

import sys

import httpx

from load_settings import load_config


def main() -> None:
    cfg = load_config()
    canvas = cfg.get("canvas") or {}
    base_url = str(canvas.get("base_url", "")).rstrip("/")
    token = str(canvas.get("access_token", "")).strip()

    if not base_url or not token or token == "REPLACE_ME":
        print("请在 config/config.yaml 中填写有效的 canvas.base_url 与 canvas.access_token。", file=sys.stderr)
        sys.exit(1)

    url = f"{base_url}/api/v1/users/self/profile"
    headers = {"Authorization": f"Bearer {token}"}

    with httpx.Client(timeout=30.0, follow_redirects=True) as client:
        resp = client.get(url, headers=headers)

    if resp.status_code == 401:
        print("401 Unauthorized：令牌无效或已过期，请在 Canvas 中重新生成访问令牌。", file=sys.stderr)
        sys.exit(1)

    try:
        resp.raise_for_status()
    except httpx.HTTPStatusError as e:
        print(f"请求失败：{e}\n{resp.text[:500]}", file=sys.stderr)
        sys.exit(1)

    data = resp.json()
    name = data.get("name")
    login_id = data.get("login_id")
    print(f"连接成功：{name} ({login_id})")


if __name__ == "__main__":
    main()
