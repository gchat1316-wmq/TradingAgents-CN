#!/usr/bin/env python3
"""
Kimi Code API 连通性测试 Demo

使用 OpenAI 兼容协议调用 Kimi Code API：
  - Base URL: https://api.kimi.com/coding/v1
  - 模型 ID: kimi-for-coding

运行前请设置环境变量：
  export KIMI_CODE_API_KEY="你的API Key"

然后执行：
  python test_kimi_code_api.py
"""

import os
import sys


def test_with_openai_sdk():
    """使用 openai 官方 SDK 调用"""
    try:
        from openai import OpenAI
    except ImportError:
        print("❌ 缺少 openai 包，请先安装: pip install openai")
        sys.exit(1)

    api_key = os.environ.get("KIMI_CODE_API_KEY")
    if not api_key:
        print("❌ 未设置 KIMI_CODE_API_KEY 环境变量")
        print("   请先在 Kimi Code 控制台创建 API Key，然后执行:")
        print('   export KIMI_CODE_API_KEY="your-api-key"')
        sys.exit(1)

    client = OpenAI(
        api_key=api_key,
        base_url="https://api.kimi.com/coding/v1",
    )

    print("🚀 正在调用 Kimi Code API (OpenAI 兼容协议)...")
    print("-" * 50)

    try:
        response = client.chat.completions.create(
            model="kimi-for-coding",
            messages=[
                {"role": "system", "content": "You are a helpful coding assistant."},
                {"role": "user", "content": "请用一句话介绍一下自己，并告诉我当前能帮你做什么。"},
            ],
            temperature=0.3,
            max_tokens=200,
        )

        print("✅ API 调用成功")
        print(f"模型: {response.model}")
        print(f"消耗 tokens: {response.usage.total_tokens if response.usage else 'N/A'}")
        print("回复内容:")
        print("-" * 50)
        print(response.choices[0].message.content)
        print("-" * 50)

    except Exception as e:
        print(f"❌ API 调用失败: {e}")
        sys.exit(1)


def test_with_requests():
    """使用原生 requests 调用（无需安装 openai 包）"""
    try:
        import requests
    except ImportError:
        print("❌ 缺少 requests 包，请先安装: pip install requests")
        sys.exit(1)

    api_key = os.environ.get("KIMI_CODE_API_KEY")
    if not api_key:
        print("❌ 未设置 KIMI_CODE_API_KEY 环境变量")
        print("   请先在 Kimi Code 控制台创建 API Key，然后执行:")
        print('   export KIMI_CODE_API_KEY="your-api-key"')
        sys.exit(1)

    url = "https://api.kimi.com/coding/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    payload = {
        "model": "kimi-for-coding",
        "messages": [
            {"role": "system", "content": "You are a helpful coding assistant."},
            {"role": "user", "content": "你好，请做个简单的自我介绍。"},
        ],
        "temperature": 0.3,
        "max_tokens": 200,
    }

    print("🚀 正在调用 Kimi Code API (requests 原生调用)...")
    print("-" * 50)

    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=60)
        resp.raise_for_status()
        data = resp.json()

        print("✅ API 调用成功")
        print(f"模型: {data.get('model', 'N/A')}")
        usage = data.get("usage", {})
        print(f"消耗 tokens: {usage.get('total_tokens', 'N/A')}")
        print("回复内容:")
        print("-" * 50)
        print(data["choices"][0]["message"]["content"])
        print("-" * 50)

    except requests.exceptions.HTTPError as e:
        print(f"❌ HTTP 错误: {e}")
        print(f"响应内容: {e.response.text}")
        sys.exit(1)
    except Exception as e:
        print(f"❌ API 调用失败: {e}")
        sys.exit(1)


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Kimi Code API 连通性测试")
    parser.add_argument(
        "--requests",
        action="store_true",
        help="使用 requests 原生调用（默认使用 openai SDK）",
    )
    args = parser.parse_args()

    if args.requests:
        test_with_requests()
    else:
        test_with_openai_sdk()
